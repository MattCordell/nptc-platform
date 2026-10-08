"""Every migration that writes `property_value` calls the foreign-code guard (FR-13).

`nptc_indexer` owns `property_value` and can plant code that runs as the next writer, which in a
migration is the migration role. `nptc.db.migration_guards.refuse_foreign_code_on_property_value`
is the check, and nothing but this test makes a future migration call it.

Pure `ast`, no container, in the style of `test_audit_write_path_guard.py`, with positive controls
so a change that makes the scan match nothing fails loudly. It reads string literals (an implicitly
concatenated literal is one constant, so a statement split across lines is still found) and
`op.bulk_insert` calls. A write built any other way, such as a SQLAlchemy `insert()` construct, is
not recognised; use SQL text or `bulk_insert` in a migration that writes this table.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = REPO_ROOT / "backend" / "migrations" / "versions"
_GUARD = "refuse_foreign_code_on_property_value"

_WRITE_RE = re.compile(
    r"\b(insert\s+into|update|delete\s+from|truncate(\s+table)?)\s+(only\s+)?"
    r"(public\.)?\"?property_value\b",
    re.IGNORECASE,
)


def writes_property_value(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _WRITE_RE.search(node.value):
                return True
        elif isinstance(node, ast.Call) and _is_named(node.func, "bulk_insert"):
            if "property_value" in ast.unparse(node):
                return True
    return False


def calls_guard(source: str) -> bool:
    return any(
        isinstance(node, ast.Call) and _is_named(node.func, _GUARD)
        for node in ast.walk(ast.parse(source))
    )


def _is_named(func: ast.expr, name: str) -> bool:
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _migrations() -> list[Path]:
    return sorted(VERSIONS.glob("*.py"))


@pytest.mark.req("FR-13")
def test_every_migration_that_writes_property_value_calls_the_guard() -> None:
    unguarded = [
        path.name
        for path in _migrations()
        if writes_property_value(path.read_text(encoding="utf-8"))
        and not calls_guard(path.read_text(encoding="utf-8"))
    ]

    assert unguarded == [], (
        "these migrations write property_value without calling "
        f"nptc.db.migration_guards.{_GUARD} first: {unguarded}"
    )


@pytest.mark.req("FR-13")
def test_the_scan_finds_the_migration_known_to_write_property_value() -> None:
    writers = [
        path.name
        for path in _migrations()
        if writes_property_value(path.read_text(encoding="utf-8"))
    ]

    assert "0024_retire_specimen_unconstrained.py" in writers


@pytest.mark.req("FR-13")
@pytest.mark.parametrize(
    "source",
    [
        'op.execute("INSERT INTO property_value (entry_id) VALUES (1)")',
        'op.execute("update property_value SET ordinal = 1")',
        'op.execute("DELETE FROM public.property_value")',
        'op.execute("TRUNCATE TABLE property_value")',
        'op.execute("DELETE FROM " "property_value WHERE x")',
        "op.bulk_insert(property_value_table, rows)",
    ],
    ids=["insert", "update", "delete", "truncate", "split-literal", "bulk-insert"],
)
def test_the_scan_recognises_each_form_of_write(source: str) -> None:
    assert writes_property_value(source)


@pytest.mark.req("FR-13")
@pytest.mark.parametrize(
    "source",
    [
        'op.execute("SELECT count(*) FROM property_value")',
        'op.execute("ALTER TABLE property_value OWNER TO someone")',
        'op.execute("INSERT INTO property_definition (key) VALUES (1)")',
    ],
    ids=["select", "alter", "other-table"],
)
def test_the_scan_ignores_what_is_not_a_write(source: str) -> None:
    assert not writes_property_value(source)


@pytest.mark.req("FR-13")
def test_the_scan_sees_a_guard_call_however_it_is_imported() -> None:
    assert calls_guard(f"{_GUARD}(connection)")
    assert calls_guard(f"migration_guards.{_GUARD}(connection)")
    assert not calls_guard("connection.execute(1)")
