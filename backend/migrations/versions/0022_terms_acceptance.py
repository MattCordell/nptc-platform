"""terms_acceptance

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-06

NFR-45, ADR-0043: the append-only record of which terms version each user accepted. See
`nptc.db.models.terms_acceptance` for the reasoning.

The grant is `SELECT, INSERT` only, so "never edited or removed" holds at the privilege level.
The downgrade drops the table, which discards the acceptance record.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terms_acceptance",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(version)) > 0", name=op.f("ck_terms_acceptance_version_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_terms_acceptance_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_terms_acceptance")),
    )
    op.create_index("ix_terms_acceptance_user_id_id", "terms_acceptance", ["user_id", "id"])

    op.execute(roles.GRANT_TERMS_ACCEPTANCE_SQL)
    op.execute(roles.REVOKE_TERMS_ACCEPTANCE_WRITE_SQL)


def downgrade() -> None:
    op.drop_index("ix_terms_acceptance_user_id_id", table_name="terms_acceptance")
    op.drop_table("terms_acceptance")
