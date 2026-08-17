"""user and user_identity

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

The privilege grants live in this same migration, not a later
"permissions" one - see 0002_audit_event.py's docstring; the same
grants-live-with-the-table reasoning (ADR-0011) applies here.

NFR-17 needs UPDATE on app_user (to write the tombstone) and DELETE on
user_identity (to remove the link) - and conspicuously **no DELETE on
app_user**. Refusing it at the privilege level makes "pseudonymise, never
delete" a database invariant rather than an application convention.
Column-level UPDATE on app_user excludes `id` and `created_at`, making the
retained UUID immutable - exactly what audit attribution and the NFR-10
hash chain depend on.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("organisation", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','suspended','closed')", name=op.f("ck_app_user_status")
        ),
        sa.CheckConstraint(
            "(status = 'closed' AND username IS NULL AND display_name IS NULL "
            "AND organisation IS NULL) "
            "OR (status <> 'closed' AND username IS NOT NULL AND display_name IS NOT NULL)",
            name=op.f("ck_app_user_tombstone"),
        ),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name=op.f("ck_app_user_closed_at")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("username", name=op.f("uq_app_user_username")),
    )
    op.create_table(
        "user_identity",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(issuer)) > 0", name=op.f("ck_user_identity_issuer_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(subject)) > 0", name=op.f("ck_user_identity_subject_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_user_identity_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identity")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("uq_user_identity_issuer")),
    )
    op.create_index(op.f("ix_user_identity_user_id"), "user_identity", ["user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_audit_event_actor_user_id_app_user"),
        "audit_event",
        "app_user",
        ["actor_user_id"],
        ["id"],
    )

    op.execute(roles.GRANT_APP_USER_SQL)
    op.execute(roles.GRANT_APP_USER_UPDATE_SQL)
    op.execute(roles.REVOKE_APP_USER_DELETE_SQL)
    op.execute(roles.GRANT_USER_IDENTITY_SQL)
    op.execute(roles.REVOKE_USER_IDENTITY_TRUNCATE_SQL)


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_audit_event_actor_user_id_app_user"), "audit_event", type_="foreignkey"
    )
    op.drop_table("user_identity")
    op.drop_table("app_user")
