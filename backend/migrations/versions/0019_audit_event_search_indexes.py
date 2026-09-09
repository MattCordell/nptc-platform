"""audit_event_search_indexes

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-10

Issue #286 (NFR-12): supporting indexes for `nptc.audit.queries.
search_audit_events`'s three other filters - actor, action, and
`occurred_at` range - each paired with `sequence` for the same keyset-
pagination reason `ix_audit_event_entity_type_entity_id_sequence`
(migration 0018) already established. See
`nptc.db.models.audit.AuditEvent.__table_args__`'s own comment.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_event_actor_user_id_sequence",
        "audit_event",
        ["actor_user_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_audit_event_action_sequence",
        "audit_event",
        ["action", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_audit_event_occurred_at_sequence",
        "audit_event",
        ["occurred_at", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_occurred_at_sequence", table_name="audit_event")
    op.drop_index("ix_audit_event_action_sequence", table_name="audit_event")
    op.drop_index("ix_audit_event_actor_user_id_sequence", table_name="audit_event")
