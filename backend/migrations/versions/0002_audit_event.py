"""audit event

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16 17:01:56.430755

The privilege grant lives in this same migration, not a later
"permissions" one: table ACLs (pg_class.relacl) are cluster state that
lives and dies with the table itself, so keeping the grant here is what
makes it survive a `downgrade base` -> `upgrade head` round-trip. A
separate migration would leave the re-created table with no grant at all
after that round-trip - exactly the shape #35's re-assertion criterion
exists to catch.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sa.UniqueConstraint("sequence", name=op.f("uq_audit_event_sequence")),
    )
    op.execute(roles.GRANT_AUDIT_EVENT_SQL)
    # Belt-and-braces: nothing granted them by the statement above. See
    # roles.py - this is the literal string NFR-22's guard greps for to
    # enforce that no migration ever grants ALL on this table.
    op.execute(roles.REVOKE_AUDIT_EVENT_WRITE_SQL)


def downgrade() -> None:
    op.drop_table("audit_event")
