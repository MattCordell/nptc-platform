"""The `terms_acceptance` table: which terms version a user accepted, and when (NFR-45, ADR-0043).

Append-only, one row per acceptance: `nptc.db.roles.REVOKE_TERMS_ACCEPTANCE_WRITE_SQL` makes that a
privilege. A column on `app_user` would keep only the latest, and NFR-45 keeps the prior record.
The row holds the internal user id and no personal data, so it survives account closure (NFR-17).

The latest row decides, and `id` (an identity column) says which is latest, because `accepted_at`
could tie inside one transaction. `version` is compared for equality, so moving the current
version back also asks for acceptance again. `(user_id, id)` serves the gate's per-request read.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["TermsAcceptance"]

_VERSION_NOT_BLANK_SQL = "length(btrim(version)) > 0"


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptance"

    # nptc.audit.policy (NFR-08): `accepted_at` repeats the event's own `occurred_at`.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"user_id", "version"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "accepted_at"})

    __table_args__ = (
        CheckConstraint(_VERSION_NOT_BLANK_SQL, name="version_not_blank"),
        Index("ix_terms_acceptance_user_id_id", "user_id", "id"),
    )

    # Identity, as for `AuditEvent.sequence`: INSERT suffices, with no `GRANT USAGE ON SEQUENCE`.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    version: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # `clock_timestamp()`, not `now()`: the instant of acceptance, not the start of a transaction.
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
