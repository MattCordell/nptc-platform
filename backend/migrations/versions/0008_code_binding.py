"""code_binding

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-21

The privilege grants live in this same migration, not a later
"permissions" one - see 0002/0003/0005/0006/0007's docstrings, the same
grants-live-with-the-table reasoning (ADR-0011) applies here.

Issue #48 (FR-06, FR-08, FR-82, FR-83): the platform's record of a SNOMED
CT code binding, stored exactly as served - see
`nptc.db.models.code_binding`'s own module docstring for the full
reasoning, and `nptc.db.models.designation`'s "three preferred-term-shaped
strings" note (ADR-0022) for why this table, and not `designation`, is
where a served label lives.

**Creates the repo's first database function.** FR-06 requires the `code`
column's own check constraint to enforce both `^\\d{6,18}$` and the
Verhoeff check digit, and a Postgres `CHECK` permits no subquery or CTE -
the Verhoeff fold (a table lookup per digit) cannot be spelled as a plain
inline expression the way `ck_designation_language`'s regex manages. This
is a narrow, deliberate exception to PRD SS14.1's ban on business logic in
database functions, not a silent precedent - see
`docs/adr/0023-database-level-sctid-validation.md` for the argument, and
`nptc.db.functions` for the function body itself, which must be created
before `code_binding` so the table's own `CHECK` can reference it. The
downgrade order is the exact reverse: the table (and its dependent CHECK)
is dropped before the function it depends on.

Four invariants enforced at the database layer, not merely by application
convention:

- `code` matches `^[0-9]{6,18}$` AND passes Verhoeff - `nptc_sctid_is_valid`
  (FR-06). `code` stays `TEXT` throughout, never a numeric type.
- `fsn`/`au_preferred_term` carry no cleaning/stripping `CHECK` beyond
  "not blank if present" - unlike `designation.term`, nothing here may
  transform a served label at rest (FR-82).
- `retirement_reason` is mandatory exactly when `status = 'retired'`, and
  `replaced_by_binding_id` may only be set on a retired binding, never on
  itself (FR-08).
- At most one active binding per entry - a partial unique index scoped
  `WHERE status = 'active'`, mirroring 0007's own partial-unique-index
  precedent for designations.

Column-level `UPDATE` excludes `entry_id`, `system` and `code` - rebinding
to a different concept is a retire-and-replace, never an in-place edit
(the same precedent 0006 already set for `catalogue_entry.business_key`).
`fsn`/`au_preferred_term` remain updatable so the FR-45 validation sweep
can refresh a drifted served label from the terminology server. No
`DELETE`/`TRUNCATE` grant at all - a binding is retired via `status`,
never removed (FR-08: "the superseded binding is retained").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import functions, roles

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Must precede create_table: the table's own `ck_code_binding_code`
    # references this function.
    op.execute(functions.CREATE_SCTID_VALIDATION_FUNCTION_SQL)

    op.create_table(
        "code_binding",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column(
            "system",
            sa.Text(),
            server_default=sa.text("'http://snomed.info/sct'"),
            nullable=False,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("fsn", sa.Text(), nullable=False),
        sa.Column("au_preferred_term", sa.Text(), nullable=True),
        sa.Column("edition_hint", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("replaced_by_binding_id", sa.UUID(), nullable=True),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(system)) > 0",
            name=op.f("ck_code_binding_system_not_blank"),
        ),
        sa.CheckConstraint(
            "nptc_sctid_is_valid(code)",
            name=op.f("ck_code_binding_code"),
        ),
        sa.CheckConstraint(
            "length(btrim(fsn)) > 0",
            name=op.f("ck_code_binding_fsn_not_blank"),
        ),
        sa.CheckConstraint(
            "au_preferred_term IS NULL OR length(btrim(au_preferred_term)) > 0",
            name=op.f("ck_code_binding_au_preferred_term_not_blank"),
        ),
        sa.CheckConstraint(
            "edition_hint IN ('au','int','unknown')",
            name=op.f("ck_code_binding_edition_hint"),
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')",
            name=op.f("ck_code_binding_status"),
        ),
        sa.CheckConstraint(
            "(status = 'retired') = "
            "(retirement_reason IS NOT NULL AND length(btrim(retirement_reason)) > 0)",
            name=op.f("ck_code_binding_retirement_reason"),
        ),
        sa.CheckConstraint(
            "replaced_by_binding_id IS NULL OR status = 'retired'",
            name=op.f("ck_code_binding_replaced_by_requires_retired"),
        ),
        sa.CheckConstraint(
            "replaced_by_binding_id IS NULL OR replaced_by_binding_id <> id",
            name=op.f("ck_code_binding_no_self_supersession"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_code_binding_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_binding_id"],
            ["code_binding.id"],
            name=op.f("fk_code_binding_replaced_by_binding_id_code_binding"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_binding")),
    )

    op.create_index(op.f("ix_code_binding_entry_id"), "code_binding", ["entry_id"], unique=False)
    op.create_index(
        "ix_code_binding_one_active_per_entry",
        "code_binding",
        ["entry_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.execute(roles.GRANT_CODE_BINDING_SQL)
    op.execute(roles.GRANT_CODE_BINDING_UPDATE_SQL)
    op.execute(roles.REVOKE_CODE_BINDING_DELETE_SQL)


def downgrade() -> None:
    op.drop_table("code_binding")
    op.execute(functions.DROP_SCTID_VALIDATION_FUNCTION_SQL)
