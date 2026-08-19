"""catalogue_entry

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19

The privilege grants live in this same migration, not a later
"permissions" one - see 0002_audit_event.py's/0003's/0005's docstrings;
the same grants-live-with-the-table reasoning (ADR-0011) applies here.

Issue #46 (FR-03, FR-38): `catalogue_entry` is the platform's central
entity. Two invariants are enforced at the database layer, not merely by
application convention:

- `business_key` (FR-03) is immutable and never reused: `UNIQUE`, no
  column-level `UPDATE` grant, and no `DELETE`/`TRUNCATE` grant at all -
  see `nptc.db.roles`'s comments on each constant for the full reasoning.
- `row_version` (FR-38) is bumped by exactly one write path - SQLAlchemy's
  `version_id_col` machinery on `CatalogueEntry`'s mapped `UPDATE` - which
  is why `row_version` sits inside the column-level `UPDATE` grant rather
  than being excluded alongside `business_key`.

`catalogue_entry_business_key_seq` backs `nptc.catalogue.entries.
allocate_business_key`'s `nextval()` call - a plain sequence, not an
`IDENTITY` column, because the application must read the next value and
format it into `NPTC-######` *before* the row exists, unlike
`audit_event.sequence`'s `IDENTITY` column (see that model's own comment on
why an identity column's backing sequence needs no separate grant, and a
plain sequence does). `ALTER SEQUENCE ... OWNED BY` ties the sequence's
lifetime to the column, so `downgrade()`'s `drop_table` takes it with it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_SEQUENCE_SQL = "CREATE SEQUENCE catalogue_entry_business_key_seq AS BIGINT START 1;"
_OWN_SEQUENCE_SQL = (
    "ALTER SEQUENCE catalogue_entry_business_key_seq OWNED BY catalogue_entry.business_key;"
)


def upgrade() -> None:
    op.execute(_CREATE_SEQUENCE_SQL)

    op.create_table(
        "catalogue_entry",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_key", sa.Text(), nullable=False),
        sa.Column("preferred_term", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column(
            "specimen_unconstrained",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','active','deprecated','withdrawn')",
            name=op.f("ck_catalogue_entry_status"),
        ),
        sa.CheckConstraint(
            "business_key ~ '^NPTC-[0-9]{6,}$'",
            name=op.f("ck_catalogue_entry_business_key"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalogue_entry")),
        sa.UniqueConstraint("business_key", name=op.f("uq_catalogue_entry_business_key")),
    )

    op.execute(_OWN_SEQUENCE_SQL)

    op.execute(roles.GRANT_CATALOGUE_ENTRY_SQL)
    op.execute(roles.GRANT_CATALOGUE_ENTRY_UPDATE_SQL)
    op.execute(roles.REVOKE_CATALOGUE_ENTRY_DELETE_SQL)
    op.execute(roles.GRANT_CATALOGUE_BUSINESS_KEY_SEQ_SQL)


def downgrade() -> None:
    # The sequence is OWNED BY catalogue_entry.business_key, so
    # drop_table takes it with it - no separate DROP SEQUENCE needed, and
    # attempting one after drop_table would fail against an
    # already-dropped object.
    op.drop_table("catalogue_entry")
