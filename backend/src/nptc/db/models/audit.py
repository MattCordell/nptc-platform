"""The audit_event table (issue #33) plus its hash chain (issue #36).

NFR-08 faithful (every state-changing write emits an event); the
TRUNCATE-refusal re-assertion after a downgrade/upgrade round-trip stays
with #35. This table's own privilege grant/revoke lives in the migration
that creates it (0002_audit_event.py), never here - an ORM model has no
way to express a table ACL, and table ACLs (pg_class.relacl) are cluster
state that lives and dies with the table itself.

``prev_hash``/``entry_hash`` (NFR-10) are added by migration
0004_audit_event_hash_chain.py. Both are ``TEXT NOT NULL`` with a
``CHECK`` constraint pinning them to 64 lowercase hex characters
(a SHA-256 digest), and ``entry_hash`` is additionally ``UNIQUE`` - see
``nptc.audit.hashing``/``nptc.audit.writer`` for the digest construction
and append sequence, and ``docs/architecture/data-model.md`` for the full
design writeup.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_event"

    # nptc.audit.policy (issue #37): exempt, not merely undeclared - this
    # table *is* the log, so diffing it would be circular. `None` plus a
    # mandatory __audit_exempt_reason__ is how a deliberate exemption is
    # told apart from a model someone forgot to classify -
    # test_audit_redaction.py's model-coverage walk requires exactly this
    # shape for every mapped class.
    __audit_fields__: ClassVar[frozenset[str] | None] = None
    __audit_exempt_reason__: ClassVar[str] = (
        "audit_event is the audit log itself; diffing it is circular"
    )

    __table_args__ = (
        # Constraint text is a plain string literal, matched verbatim in
        # migration 0004_audit_event_hash_chain.py - see User's own
        # __table_args__ for the same NFR-22 rationale. 64 lowercase hex
        # characters is the textual shape of a SHA-256 digest.
        CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name="prev_hash_hex"),
        CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="entry_hash_hex"),
    )

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
    # Nullable FK to app_user (issue #42): a system-initiated event has no
    # human actor. Never deleted, only pseudonymised (NFR-17) - the FK is
    # what makes "pseudonymise, never delete" structural rather than an
    # application convention: app_user.id survives closure unchanged, so
    # this reference is never dangling.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True
    )
    actor_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NFR-10 hash chain (issue #36). No server default on either column:
    # there is no way to invent a hash for a pre-existing row, so this
    # migration only ever applies to an empty audit_event (pre-alpha, no
    # write path has ever run - see docs/operations/upgrade.md).
    # `nptc.audit.hashing.GENESIS_HASH` (64 `0`s) is the first row's
    # prev_hash. entry_hash is UNIQUE, which is what makes the chain a
    # path rather than a DAG and a replayed row structurally impossible.
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
