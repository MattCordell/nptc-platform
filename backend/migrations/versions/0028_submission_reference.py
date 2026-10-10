"""submission reference link

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-10

FR-27: a new-test submission carries a supporting reference link, with when the server checked it
and the HTTP status it answered. See `nptc.db.models.submission` for the reasoning.

The three columns are nullable, because an amendment may leave the reference out. A submission
saved at revision 0027 has none, so the rule that a new test must carry one is added `NOT VALID`:
the database enforces it for every row written from now on and leaves existing rows alone.

The grants need no change. The app role's `UPDATE` is limited to `state`, `updated_at` and
`row_version`, so the new columns are fixed after insert like every other content column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("submission", sa.Column("reference_url", sa.Text(), nullable=True))
    op.add_column(
        "submission", sa.Column("reference_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("submission", sa.Column("reference_status", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_submission_reference_url_not_blank"),
        "submission",
        "reference_url IS NULL OR length(btrim(reference_url)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_submission_reference_check_complete"),
        "submission",
        "(reference_url IS NULL) = (reference_checked_at IS NULL) "
        "AND (reference_url IS NULL) = (reference_status IS NULL)",
    )
    op.execute(
        "ALTER TABLE submission ADD CONSTRAINT ck_submission_new_test_has_reference "
        "CHECK (kind <> 'new_test' OR reference_url IS NOT NULL) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_submission_new_test_has_reference", "submission", type_="check")
    op.drop_constraint("ck_submission_reference_check_complete", "submission", type_="check")
    op.drop_constraint("ck_submission_reference_url_not_blank", "submission", type_="check")
    op.drop_column("submission", "reference_status")
    op.drop_column("submission", "reference_checked_at")
    op.drop_column("submission", "reference_url")
