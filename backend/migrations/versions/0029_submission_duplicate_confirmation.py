"""submission duplicate confirmation

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-10

FR-25: a submission that matched something already in the catalogue or already proposed is stored
only when the request confirmed it, with the time of the confirmation and the matches the server
found when it saved the row. See `nptc.db.models.submission` for the reasoning.

`duplicate_matches` defaults to an empty array, so a submission saved at revision 0028 reads as one
that matched nothing. `duplicate_confirmed_at` is present exactly when the array is not empty.

The grants need no change. The app role's `UPDATE` is limited to `state`, `updated_at` and
`row_version`, so the new columns are fixed after insert like every other content column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submission", sa.Column("duplicate_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "submission",
        sa.Column(
            "duplicate_matches",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_submission_duplicate_matches_is_array"),
        "submission",
        "jsonb_typeof(duplicate_matches) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_submission_duplicate_confirmation_complete"),
        "submission",
        "(duplicate_confirmed_at IS NULL) = (duplicate_matches = '[]'::jsonb)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_submission_duplicate_confirmation_complete"), "submission", type_="check"
    )
    op.drop_constraint(
        op.f("ck_submission_duplicate_matches_is_array"), "submission", type_="check"
    )
    op.drop_column("submission", "duplicate_matches")
    op.drop_column("submission", "duplicate_confirmed_at")
