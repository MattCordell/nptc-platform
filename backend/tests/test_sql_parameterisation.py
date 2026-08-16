"""NFR-22 guard: no string-built SQL anywhere in backend/src or
backend/migrations (issue #33).

Pure ``ast``, no container, no network access - this walks source files, it
never touches a database. Reports every violation with ``file:line``
(mirroring ``scripts/traceability_check.py``'s "print the whole list"
convention) rather than stopping at the first one.

Three rules:

1. Argument 0 of a ``text(...)``, ``.execute(...)`` or
   ``.exec_driver_sql(...)`` call must not be built from runtime data: an
   f-string (``JoinedStr``), ``%``/``+`` concatenation (``BinOp``), or a
   ``.format()`` call are all rejected. A plain string literal, or a
   Name/Attribute reference to a module-level constant (e.g.
   ``nptc.db.roles.GRANT_AUDIT_EVENT_SQL``, imported precisely so the
   migration and its tests share one source of truth) both pass - the
   concern is runtime data reaching a query, not where the fixed literal
   text happens to be spelled.
2. Any f-string *anywhere* (not just at a call site) whose literal parts
   *start with* a SQL keyword fails - this catches SQL assembled into a
   variable above the call, which rule 1 alone would miss since the call
   site only ever sees a bare ``Name``. Anchored to the start rather than
   matched anywhere in the text: a real SQL statement starts with its verb,
   and anchoring is what keeps an unrelated f-string like
   ``f"failed to update {entity_id}"`` (a plausible future log message) from
   tripping this rule the moment it happens to contain a keyword mid-sentence.
3. Any string literal combining "GRANT ALL" with the ``audit_event`` table
   name fails outright (NFR-09 riding along on NFR-22's own machinery) -
   TRUNCATE is a distinct, owner-only privilege included in ``ALL`` and
   must never be granted to the app role on an audit table.

Ships with its own positive control
(``test_guard_flags_known_violations``) run over an inline source string,
not real files - so a refactor that quietly makes the walker match nothing
still fails loudly, rather than this genre of test rotting into one that
always passes (CLAUDE.md's "principal failure mode" rule).
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "backend" / "src", REPO_ROOT / "backend" / "migrations"]

_SQL_KEYWORD_RE = re.compile(
    r"^\s*(select|insert|update|delete|drop|create|alter|grant|revoke|truncate)\b",
    re.IGNORECASE,
)
_GRANT_ALL_RE = re.compile(r"grant\s+all", re.IGNORECASE)
_SQL_CALL_ATTRS = frozenset({"execute", "exec_driver_sql"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _joined_str_literal_text(node: ast.JoinedStr) -> str:
    """Concatenates only the literal (non-interpolated) parts of an
    f-string - the interpolated parts are what makes it dangerous, not
    what identifies the literal text as SQL."""
    return "".join(
        part.value
        for part in node.values
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


def _dynamic_sql_reason(node: ast.expr) -> str | None:
    """Returns why `node` is SQL text built from runtime data, or None if
    it's safe (a literal, or a reference to one defined elsewhere)."""
    if isinstance(node, ast.JoinedStr):
        return "f-string"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return "string concatenation (%/+)"
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return ".format() call"
    return None


def _is_sql_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "text":
        return True
    return isinstance(func, ast.Attribute) and func.attr in _SQL_CALL_ATTRS


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _check_source(source: str, display_path: str) -> list[Violation]:
    violations: list[Violation] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_sql_call(node) and node.args:
            reason = _dynamic_sql_reason(node.args[0])
            if reason is not None:
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "dynamic-sql-arg",
                        f"SQL call's first argument is built from {reason}",
                    )
                )

        if isinstance(node, ast.JoinedStr) and _SQL_KEYWORD_RE.search(
            _joined_str_literal_text(node)
        ):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "sql-fstring",
                    "f-string's literal text contains a SQL keyword",
                )
            )

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _GRANT_ALL_RE.search(node.value)
            and "audit_event" in node.value
        ):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "grant-all-audit",
                    "GRANT ALL against the audit_event table",
                )
            )

    return violations


def _check_file(path: Path) -> list[Violation]:
    return _check_source(path.read_text(encoding="utf-8"), _display(path))


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


@pytest.mark.req("NFR-22")
def test_no_dynamic_sql_or_grant_all_in_backend_source() -> None:
    violations = [v for path in _iter_source_files() for v in _check_file(path)]

    assert not violations, "NFR-22 violation(s) found:\n" + "\n".join(str(v) for v in violations)


def test_guard_flags_known_violations() -> None:
    """A set of `{rule}` alone would let three of `_dynamic_sql_reason`'s
    four distinct detections (f-string, `+` concatenation, `.format()`
    call, and the separate rule-2 f-string-built-above-the-call case)
    collapse into a single satisfied element - deleting any one of them
    would still leave the set unchanged. Asserting an exact per-rule count
    instead means each of the four bad_source cases below has to be
    individually detected for this test to pass."""
    bad_source = """
user_id = "1 OR 1=1"


def direct_fstring():
    op.execute(f"DELETE FROM audit_event WHERE id = {user_id}")


def direct_concat():
    connection.execute("SELECT * FROM x WHERE id = " + user_id)


def direct_format():
    connection.exec_driver_sql("UPDATE t SET x = %s".format(user_id))


def built_above():
    query = f"SELECT * FROM x WHERE id = {user_id}"
    connection.execute(query)


def grant_all_on_audit():
    op.execute("GRANT ALL ON TABLE audit_event TO nptc_app;")
"""
    violations = _check_source(bad_source, "<positive-control>")
    rule_counts = Counter(v.rule for v in violations)

    # dynamic-sql-arg: direct_fstring, direct_concat, direct_format (3) -
    # built_above's call site sees only a bare Name and is rule 2's job.
    # sql-fstring: direct_fstring's and built_above's f-strings both start
    # with a SQL keyword (2). grant-all-audit: grant_all_on_audit (1).
    assert rule_counts == Counter({"dynamic-sql-arg": 3, "sql-fstring": 2, "grant-all-audit": 1})
