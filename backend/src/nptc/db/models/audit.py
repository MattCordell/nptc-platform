"""The minimal audit_event table (issue #33).

NFR-08 faithful (every state-changing write emits an event) but the hash
chain (NFR-10: prev_hash/entry_hash) and its verification stay with #36;
the TRUNCATE-refusal re-assertion after a downgrade/upgrade round-trip
stays with #35. This table's own privilege grant/revoke lives in the
migration that creates it (0002_audit_event.py), never here - an ORM model
has no way to express a table ACL, and table ACLs (pg_class.relacl) are
cluster state that lives and dies with the table itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Identity, not a `serial` default: an identity column's backing sequence
    # is an internal dependency of the column and isn't ACL-checked against
    # the inserting role, so INSERT on the table alone suffices. A `serial`
    # default is a plain nextval(...) evaluated with the *inserting* role's
    # own privileges and would silently need its own
    # GRANT USAGE ON SEQUENCE - the classic thing forgotten on a
    # re-migration. Proven empirically by
    # backend/tests/test_db_audit_privileges.py, not assumed.
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No FK to a user table: user/user_identity lands with #42. actor_user_id
    # is NULL until then (e.g. a system-initiated event has no human actor).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
