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

Two rules, both scoped to every file *except*
`backend/src/nptc/audit/writer.py` itself (which legitimately constructs
`AuditEvent` and is the writer this guard exists to keep unique):

1. Calling `AuditEvent(...)` as a constructor.
2. A string literal SQL `INSERT` targeting `audit_event`.
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


def _check_source(source: str, display_path: str) -> list[Violation]:
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

    return violations


def _check_file(path: Path) -> list[Violation]:
    return _check_source(path.read_text(encoding="utf-8"), _display(path))


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
    """Positive control: a constructor call and a raw INSERT literal, each
    in their own function, so the guard can't silently rot into a no-op
    that never fires."""
    bad_source = """
from nptc.db.models.audit import AuditEvent


def direct_constructor(session):
    event = AuditEvent(prev_hash="0" * 64, entry_hash="1" * 64)
    session.add(event)


def raw_insert(connection):
    connection.execute("INSERT INTO audit_event (action) VALUES ('x')")
"""
    violations = _check_source(bad_source, "<positive-control>")
    rule_counts = Counter(v.rule for v in violations)

    assert rule_counts == Counter({"audit-event-constructor": 1, "raw-insert-audit-event": 1})
