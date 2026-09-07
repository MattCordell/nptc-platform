"""code_binding system_code index

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-07

Issue #140 (FR-17): `nptc.catalogue.queries.get_entry_by_code` looks up a
`code_binding` by `(system, code)` with no `status` predicate - it must see
retired rows as well as active ones (FR-08), unlike every other query
against this table. Every existing `(system, code)`/`code` index is partial
on `WHERE status = 'active'`
(`0008_code_binding.py`/`0009_collision_detection.py`/`0015_hybrid_search_indexes.py`),
so none of them can be proven applicable by the planner for a query that
carries no matching predicate - the exact-code lookup route would
sequentially scan `code_binding` on every call, including the common active
case.

`ix_code_binding_system_code` is deliberately non-partial and non-unique:
non-partial because the query it serves has no predicate a partial index
could match, and non-unique because two different entries can legitimately
share the same code as *retired* bindings (a code is retired and replaced,
never rebound in place - see `docs/adr/0033-exact-code-lookup-routes.md`),
which `ix_code_binding_one_active_entry_per_code` above already permits.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_code_binding_system_code", "code_binding", ["system", "code"])


def downgrade() -> None:
    op.drop_index("ix_code_binding_system_code", table_name="code_binding")
