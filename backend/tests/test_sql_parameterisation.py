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
4. A Core ``sqlalchemy.update(...)``/``delete(...)`` call (or a raw
   ``UPDATE``/``DELETE`` string literal) whose target names a table in
   ``VERSIONED_TABLES`` fails, under ``backend/src`` only (a migration's own
   backfill is a legitimate, one-off bulk statement; a domain write path
   using one against a version-locked table is not). ADR-0012 names the
   exact hazard this closes: a Core-style bulk statement goes through the
   ORM ``Session`` but bypasses ``version_id_col`` enforcement entirely, so
   the FR-38 guarantee on ``catalogue_entry`` (issue #46) would otherwise be
   silently defeated by a future bulk-write path (e.g. #63's reclassify)
   reaching for ``session.execute(update(CatalogueEntry)...)`` instead of
   ``nptc.catalogue.entries.save_entries``.

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

from nptc.db import models as _models  # noqa: F401 - import-for-side-effect, see below
from nptc.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "backend" / "src", REPO_ROOT / "backend" / "migrations"]

_SQL_KEYWORD_RE = re.compile(
    r"^\s*(select|insert|update|delete|drop|create|alter|grant|revoke|truncate)\b",
    re.IGNORECASE,
)
_GRANT_ALL_RE = re.compile(r"grant\s+all", re.IGNORECASE)
_SQL_CALL_ATTRS = frozenset({"execute", "exec_driver_sql"})

#: Rule 4. Mapped model class names whose table carries a `version_id_col`
#: (ADR-0012) - a Core `update(...)`/`delete(...)` call naming one of these
#: bypasses that column's enforcement even though it still runs through the
#: ORM `Session`. Extend this set as more tables adopt `version_id_col`
#: (e.g. #52's `PropertyDefinition`, which ADR-0012 already documents the
#: same doctrine for) - the raw-SQL regex below is derived from this same
#: set, via `_camel_to_snake`, so the two can never silently drift apart
#: the way two independently-maintained literals could.
VERSIONED_TABLE_MODELS = frozenset({"CatalogueEntry"})
_BULK_STATEMENT_FUNCS = frozenset({"update", "delete"})
_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    """`CatalogueEntry` -> `catalogue_entry` - every model in this
    codebase names its table as the snake_case form of its class name
    (`nptc.db.models`'s own convention), so this is the one place that
    mapping is spelled out for a test that must never let a model name and
    its table name drift apart."""
    return _CAMEL_BOUNDARY_RE.sub("_", name).lower()


_VERSIONED_TABLE_NAMES = frozenset(_camel_to_snake(name) for name in VERSIONED_TABLE_MODELS)
_VERSIONED_RAW_SQL_RE = re.compile(
    r"^\s*(update|delete\s+from)\s+(" + "|".join(sorted(_VERSIONED_TABLE_NAMES)) + r")\b",
    re.IGNORECASE,
)


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


def _bulk_statement_target_name(node: ast.Call) -> str | None:
    """If `node` is a call to a bare or attribute-qualified `update`/
    `delete` (i.e. `sqlalchemy.update(...)`/`update(...)`) whose first
    argument is a `Name` or an attribute chain ending in one (e.g.
    `CatalogueEntry.__table__`), returns that leading name - the model or
    table the statement targets. `None` if this isn't that shape at all."""
    func = node.func
    is_bulk_func = (isinstance(func, ast.Name) and func.id in _BULK_STATEMENT_FUNCS) or (
        isinstance(func, ast.Attribute) and func.attr in _BULK_STATEMENT_FUNCS
    )
    if not is_bulk_func or not node.args:
        return None

    target = node.args[0]
    while isinstance(target, ast.Attribute):
        target = target.value
    if isinstance(target, ast.Name):
        return target.id
    return None


def _query_bulk_statement_target(node: ast.Call) -> str | None:
    """The other Core-bypass shape ADR-0012 warns about:
    `session.query(Model).filter(...).update(...)` /
    `...delete(...)` - `Query.update()`/`Query.delete()` also bypass
    `version_id_col` despite reading as ORM code. Walks back through any
    chained `.filter()`/`.filter_by()`/etc. calls looking for a `.query(Model)`
    call anywhere in the chain; returns `Model`'s name if found."""
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in _BULK_STATEMENT_FUNCS):
        return None

    cursor = func.value
    while isinstance(cursor, ast.Call) and isinstance(cursor.func, ast.Attribute):
        if cursor.func.attr == "query" and cursor.args and isinstance(cursor.args[0], ast.Name):
            return cursor.args[0].id
        cursor = cursor.func.value
    return None


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _check_source(
    source: str, display_path: str, *, enforce_versioned_table_rule: bool = True
) -> list[Violation]:
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

        if enforce_versioned_table_rule:
            if isinstance(node, ast.Call):
                target_name = _bulk_statement_target_name(node) or _query_bulk_statement_target(
                    node
                )
                if target_name in VERSIONED_TABLE_MODELS:
                    violations.append(
                        Violation(
                            display_path,
                            node.lineno,
                            "versioned-table-bulk-statement",
                            f"Core/Query update()/delete() against {target_name} bypasses "
                            "version_id_col (ADR-0012) - use the model's own service "
                            "layer instead",
                        )
                    )

            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _VERSIONED_RAW_SQL_RE.search(node.value)
            ):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "versioned-table-bulk-statement",
                        "raw UPDATE/DELETE against catalogue_entry bypasses "
                        "version_id_col (ADR-0012) - use the model's own service "
                        "layer instead",
                    )
                )

    return violations


def _check_file(path: Path, *, enforce_versioned_table_rule: bool = True) -> list[Violation]:
    return _check_source(
        path.read_text(encoding="utf-8"),
        _display(path),
        enforce_versioned_table_rule=enforce_versioned_table_rule,
    )


def _iter_source_files() -> list[tuple[Path, bool]]:
    """Each file paired with whether rule 4 (versioned-table bulk
    statements) applies to it - `backend/migrations` is excluded, since a
    migration's own one-off backfill is a legitimate bulk statement, unlike
    a domain write path reaching for one."""
    files: list[tuple[Path, bool]] = []
    for base in SCAN_DIRS:
        if base.is_dir():
            enforce = base.name == "src"
            files.extend((path, enforce) for path in sorted(base.rglob("*.py")))
    return files


@pytest.mark.req("NFR-22")
def test_no_dynamic_sql_or_grant_all_in_backend_source() -> None:
    violations = [
        v
        for path, enforce in _iter_source_files()
        for v in _check_file(path, enforce_versioned_table_rule=enforce)
    ]

    assert not violations, "NFR-22 violation(s) found:\n" + "\n".join(str(v) for v in violations)


@pytest.mark.req("FR-38")
def test_no_bulk_statement_against_versioned_table_in_backend_source() -> None:
    """A dedicated assertion, separate from the NFR-22 sweep above, so this
    guard's own req marker (FR-38) traces independently of NFR-22's."""
    violations = [
        v
        for path in (REPO_ROOT / "backend" / "src").rglob("*.py")
        for v in _check_file(path)
        if v.rule == "versioned-table-bulk-statement"
    ]
    assert not violations, "FR-38 versioned-table violation(s) found:\n" + "\n".join(
        str(v) for v in violations
    )


@pytest.mark.req("FR-38")
def test_versioned_table_names_are_derived_from_real_tables() -> None:
    """`_camel_to_snake` assumes every model's table name is exactly the
    snake_case form of its class name - true for every model in this
    codebase today, but an acronym-leading class name added later
    (`SCTIDBinding` -> `s_c_t_i_d_binding`) would mis-derive silently,
    quietly disabling the raw-SQL half of rule 4 for that table rather
    than failing anywhere visible. Asserting the derived name is an actual
    table in `Base.metadata` (populated by importing `nptc.db.models`,
    which imports every model for exactly this kind of check) turns that
    assumption into something that fails loudly instead."""
    for model_name in sorted(VERSIONED_TABLE_MODELS):
        table_name = _camel_to_snake(model_name)
        assert table_name in Base.metadata.tables, (
            f"_camel_to_snake({model_name!r}) produced {table_name!r}, which is not a "
            "real table in Base.metadata - VERSIONED_TABLE_MODELS and "
            "_VERSIONED_RAW_SQL_RE would silently drift apart"
        )


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


def bulk_update_versioned_table():
    session.execute(update(CatalogueEntry).values(status="withdrawn"))


def bulk_delete_versioned_table():
    session.execute(delete(CatalogueEntry))


def raw_sql_versioned_table():
    session.execute(text("UPDATE catalogue_entry SET status = 'withdrawn'"))


def query_update_versioned_table():
    session.query(CatalogueEntry).filter_by(status="draft").update({"status": "withdrawn"})
"""
    violations = _check_source(bad_source, "<positive-control>")
    rule_counts = Counter(v.rule for v in violations)

    # dynamic-sql-arg: direct_fstring, direct_concat, direct_format (3) -
    # built_above's call site sees only a bare Name and is rule 2's job.
    # sql-fstring: direct_fstring's and built_above's f-strings both start
    # with a SQL keyword (2). grant-all-audit: grant_all_on_audit (1).
    # versioned-table-bulk-statement: bulk_update_versioned_table,
    # bulk_delete_versioned_table, raw_sql_versioned_table,
    # query_update_versioned_table (4) - rule 4.
    assert rule_counts == Counter(
        {
            "dynamic-sql-arg": 3,
            "sql-fstring": 2,
            "grant-all-audit": 1,
            "versioned-table-bulk-statement": 4,
        }
    )
