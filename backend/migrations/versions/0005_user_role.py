"""user_role

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-19

The privilege grants live in this same migration, not a later
"permissions" one - see 0002_audit_event.py's / 0003's docstrings; the
same grants-live-with-the-table reasoning (ADR-0011) applies here.

Issue #44 (FR-44, FR-01): a granted role, and who granted it. No `role`
column on `app_user` - see that model's docstring. `UPDATE` is granted on
`granted_at` only, not the table as a whole: a role is created or removed,
never edited - `user_id`/`role`/`granted_by_user_id` stay immutable at the
privilege level - but Postgres requires *some* `UPDATE` privilege on a
table before it honours `SELECT ... FOR UPDATE`, which FR-01's
last-administrator guard depends on (see `nptc.db.roles.
GRANT_USER_ROLE_UPDATE_SQL`).

`UniqueConstraint("user_id", "role")` is named `uq_user_role_user_id` by
the naming convention (`column_0_name`, i.e. the *first* listed column) -
this is the same apparent-bug-but-isn't `user_identity`'s own
`uq_user_identity_issuer` documents; don't "fix" it without updating the
convention itself.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_role",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by_user_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "role IN ('observer','provisional','member','reviewer','administrator')",
            name=op.f("ck_user_role_role"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_user_role_user_id_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_user_role_granted_by_user_id_app_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_role")),
        sa.UniqueConstraint("user_id", "role", name=op.f("uq_user_role_user_id")),
    )

    op.execute(roles.GRANT_USER_ROLE_SQL)
    op.execute(roles.GRANT_USER_ROLE_UPDATE_SQL)
    op.execute(roles.REVOKE_USER_ROLE_TRUNCATE_SQL)


def downgrade() -> None:
    op.drop_table("user_role")
