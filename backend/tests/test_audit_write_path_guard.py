"""NFR-08/NFR-10 structural guard: `nptc.audit.writer.append_audit_event`
is the only sanctioned way to write `audit_event` (issue #36) - nothing
short of a structural check enforces that claim, since a bare
`AuditEvent(...)` + `session.add()` or a raw `INSERT INTO audit_event`
anywhere else under `backend/src` would otherwise pass CI silently.

Pure `ast`, no container, no network access - same style as
`test_sql_parameterisation.py`, which this module deliberately mirrors:
a fast walk over `backend/src` with its own positive control
(`test_guard_flags_known_violations`) so a refactor that quietly makes the
walker match nothing still fails loudly, rather than this genre of test
rotting into one that always passes.

Three rules, all scoped to every file *except*
`backend/src/nptc/audit/writer.py` itself (which legitimately constructs
`AuditEvent` and is the writer this guard exists to keep unique):

1. Calling `AuditEvent(...)` as a constructor.
2. A string literal SQL `INSERT` targeting `audit_event`.
3. **`audit-diff-bypass`** (issue #37) - outside `backend/src/nptc/audit/`,
   a call to `append_audit_event` carrying a `before=` or `after=` keyword
   whose value is not the literal `None`. Deliberately narrower than "no
   `append_audit_event` outside `nptc/audit/`": a diff-free event (e.g. a
   future `release.published`) is legitimate and must still be callable
   directly. What is not legitimate is a caller hand-building a
   `before`/`after` payload instead of going through
   `nptc.audit.recording.record_change`/`record_snapshot_change` - that is
   exactly the per-endpoint reimplementation issue #37 exists to close off.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIR = REPO_ROOT / "backend" / "src"
_EXEMPT_FILE = REPO_ROOT / "backend" / "src" / "nptc" / "audit" / "writer.py"

_INSERT_AUDIT_EVENT_RE = re.compile(r"^\s*insert\s+into\s+[\"']?audit_event\b", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_audit_event_constructor_call(node: ast.expr) -> bool:
    func = node.func if isinstance(node, ast.Call) else None
    if isinstance(func, ast.Name):
        return func.id == "AuditEvent"
    if isinstance(func, ast.Attribute):
        return func.attr == "AuditEvent"
    return False


def _is_append_audit_event_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "append_audit_event"
    if isinstance(func, ast.Attribute):
        return func.attr == "append_audit_event"
    return False


def _is_literal_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _hand_built_diff_keywords(node: ast.Call) -> list[ast.keyword]:
    return [
        kw
        for kw in node.keywords
        if kw.arg in ("before", "after") and not _is_literal_none(kw.value)
    ]


#: Rule 3 (`audit-diff-bypass`) is scoped to every file *outside* this
#: package - `nptc.audit` itself is where a hand-built `before`/`after`
#: payload is legitimately assembled (`nptc.audit.recording` is the thing
#: doing the assembling), not a bypass of it.
_AUDIT_PACKAGE_DIR = REPO_ROOT / "backend" / "src" / "nptc" / "audit"


def _check_source(
    source: str, display_path: str, *, in_audit_package: bool = False
) -> list[Violation]:
    violations: list[Violation] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_audit_event_constructor_call(node):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "audit-event-constructor",
                    "AuditEvent(...) constructed outside nptc.audit.writer",
                )
            )

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _INSERT_AUDIT_EVENT_RE.search(node.value)
        ):
            violations.append(
                Violation(
                    display_path,
                    node.lineno,
                    "raw-insert-audit-event",
                    "raw SQL INSERT literal targeting audit_event",
                )
            )

        if (
            not in_audit_package
            and isinstance(node, ast.Call)
            and _is_append_audit_event_call(node)
        ):
            for kw in _hand_built_diff_keywords(node):
                violations.append(
                    Violation(
                        display_path,
                        node.lineno,
                        "audit-diff-bypass",
                        f"append_audit_event(...) called with a hand-built {kw.arg}= "
                        "payload outside nptc.audit - use "
                        "nptc.audit.recording.record_change/record_snapshot_change instead",
                    )
                )

    return violations


def _check_file(path: Path) -> list[Violation]:
    resolved = path.resolve()
    in_audit_package = _AUDIT_PACKAGE_DIR in resolved.parents
    return _check_source(
        resolved.read_text(encoding="utf-8"), _display(path), in_audit_package=in_audit_package
    )


def _iter_source_files() -> list[Path]:
    if not SCAN_DIR.is_dir():
        return []
    return [path for path in sorted(SCAN_DIR.rglob("*.py")) if path.resolve() != _EXEMPT_FILE]


@pytest.mark.req("NFR-08")
@pytest.mark.req("NFR-10")
def test_no_second_unaudited_write_path_to_audit_event() -> None:
    violations = [v for path in _iter_source_files() for v in _check_file(path)]

    assert not violations, (
        "found a write path to audit_event outside nptc.audit.writer:\n"
        + "\n".join(str(v) for v in violations)
    )


def test_guard_flags_known_violations() -> None:
    """Positive control: a constructor call, a raw INSERT literal, and a
    hand-built before=/after= call to append_audit_event, each in their
    own function, so the guard can't silently rot into a no-op that never
    fires."""
    bad_source = """
from nptc.audit.writer import append_audit_event
from nptc.db.models.audit import AuditEvent


def direct_constructor(session):
    event = AuditEvent(prev_hash="0" * 64, entry_hash="1" * 64)
    session.add(event)


def raw_insert(connection):
    connection.execute("INSERT INTO audit_event (action) VALUES ('x')")


def hand_built_diff(session, ctx):
    append_audit_event(
        session,
        ctx,
        action="x.y",
        entity_type="x",
        entity_id="1",
        before={"a": 1},
        after={"a": 2},
    )
"""
    violations = _check_source(bad_source, "<positive-control>")
    rule_counts = Counter(v.rule for v in violations)

    assert rule_counts == Counter(
        {"audit-event-constructor": 1, "raw-insert-audit-event": 1, "audit-diff-bypass": 2}
    )


def test_guard_does_not_flag_a_diff_free_append_audit_event_call() -> None:
    """Negative control for `audit-diff-bypass`: `before=None`/`after=None`
    (or omitting them) is exactly the diff-free event shape
    `append_audit_event` must stay callable directly for - e.g. a future
    `release.published` with nothing to diff."""
    good_source = """
from nptc.audit.writer import append_audit_event


def diff_free_event(session, ctx):
    append_audit_event(
        session,
        ctx,
        action="release.published",
        entity_type="release",
        entity_id="1",
        before=None,
        after=None,
    )
"""
    violations = _check_source(good_source, "<negative-control>")

    assert violations == []


def test_guard_exempts_the_audit_package_itself_from_the_bypass_rule() -> None:
    """`nptc.audit.recording` is the module that legitimately builds
    `before=`/`after=` and calls `append_audit_event` with them - the
    bypass rule must not flag its own implementation."""
    source_inside_audit_package = """
from nptc.audit.writer import append_audit_event


def record_change(session, ctx):
    append_audit_event(session, ctx, action="x", entity_type="x", entity_id="1", before={"a": 1})
"""
    violations = _check_source(
        source_inside_audit_package, "<audit-package>", in_audit_package=True
    )

    assert violations == []
