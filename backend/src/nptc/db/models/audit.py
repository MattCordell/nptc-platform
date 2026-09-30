"""The `audit_event` table and its hash chain (NFR-08, NFR-10).

This table's privilege grant and revoke live in the migration that creates it
(`0002_audit_event.py`), never here: an ORM model cannot express a table ACL, and ACLs
(`pg_class.relacl`) live and die with the table.

`prev_hash` and `entry_hash` come from migration `0004_audit_event_hash_chain.py`. Both are `TEXT
NOT NULL` with a `CHECK` pinning them to 64 lowercase hex characters (a SHA-256 digest), and
`entry_hash` is also `UNIQUE`. See `nptc.audit.hashing` and `nptc.audit.writer` for the digest and
append sequence, and `docs/architecture/data-model.md` for the design.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_event"

    # nptc.audit.policy: exempt, not merely undeclared. This table is the log, so diffing it would
    # be circular. `None` plus a mandatory `__audit_exempt_reason__` tells a deliberate exemption
    # from a forgotten model; `test_audit_redaction.py`'s model-coverage walk requires that shape
    # for every mapped class.
    __audit_fields__: ClassVar[frozenset[str] | None] = None
    __audit_exempt_reason__: ClassVar[str] = (
        "audit_event is the audit log itself; diffing it is circular"
    )

    __table_args__ = (
        # A plain string literal, matched verbatim in migration 0004 (see `User.__table_args__` for
        # the NFR-22 rationale). 64 lowercase hex characters is a SHA-256 digest.
        CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name="prev_hash_hex"),
        CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="entry_hash_hex"),
        # Migration 0018: `nptc.catalogue.history.load_history` reads this table from an anonymous
        # endpoint, and the only earlier index was `sequence`'s `UNIQUE`, useless for a query that
        # equates on `entity_type` and `entity_id` first. Plain ascending: a btree is scanned
        # backwards at the same cost, so `ORDER BY sequence DESC` is served, and the declaration
        # stays textually identical to the migration's `op.create_index`, which
        # `test_upgrade_head_matches_models` compares.
        Index(
            "ix_audit_event_entity_type_entity_id_sequence",
            "entity_type",
            "entity_id",
            "sequence",
        ),
        # Migration 0019 (NFR-12): the other three filters of
        # `nptc.audit.queries.search_audit_events`, each paired with `sequence` for the reasons
        # above.
        Index("ix_audit_event_actor_user_id_sequence", "actor_user_id", "sequence"),
        Index("ix_audit_event_action_sequence", "action", "sequence"),
        Index("ix_audit_event_occurred_at_sequence", "occurred_at", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Identity, not a `serial` default: an identity column's sequence is not ACL-checked against the
    # inserting role, so INSERT on the table suffices. A `serial` default is evaluated with the
    # inserting role's privileges and would need its own `GRANT USAGE ON SEQUENCE`.
    # `backend/tests/test_db_audit_privileges.py` proves it.
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Nullable: a system-initiated event has no human actor. Never deleted, only pseudonymised
    # (NFR-17); the FK makes that structural, because `app_user.id` survives closure unchanged.
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
    # NFR-10 hash chain. No server default: a hash cannot be invented for an existing row, so the
    # migration applies only to an empty `audit_event` (see `docs/operations/upgrade.md`).
    # `nptc.audit.hashing.GENESIS_HASH` (64 `0`s) is the first row's `prev_hash`. `entry_hash` is
    # UNIQUE, which makes the chain a path rather than a DAG and a replayed row impossible.
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
