"""submission amendment entry link

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-10

FR-35: an amendment names the catalogue entry it proposes to change. See
`nptc.db.models.submission` for the reasoning.

The column is nullable, and a check ties it to `kind`: an amendment has an entry and a new test has
none. No amendment can exist at revision 0029, because no route created one, so every stored row
is a new test and satisfies the check without a backfill.

The grants need no change. The app role's `UPDATE` is limited to `state`, `updated_at` and
`row_version`, so the new column is fixed after insert like every other content column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submission", sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_submission_entry_id_catalogue_entry"),
        "submission",
        "catalogue_entry",
        ["entry_id"],
        ["id"],
    )
    op.create_index(op.f("ix_submission_entry_id"), "submission", ["entry_id"])
    op.create_check_constraint(
        op.f("ck_submission_amendment_links_entry"),
        "submission",
        "(kind = 'amendment') = (entry_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_submission_amendment_links_entry"), "submission", type_="check")
    op.drop_index(op.f("ix_submission_entry_id"), table_name="submission")
    op.drop_constraint(
        op.f("fk_submission_entry_id_catalogue_entry"), "submission", type_="foreignkey"
    )
    op.drop_column("submission", "entry_id")
