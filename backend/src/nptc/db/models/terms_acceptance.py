"""The `terms_acceptance` table: which terms version a user accepted, and when (NFR-45, ADR-0043).

**Append-only, one row per acceptance.** A column on `app_user` would hold only the latest
acceptance, and NFR-45 requires the prior record to be kept. `nptc.db.roles.
REVOKE_TERMS_ACCEPTANCE_WRITE_SQL` makes "insert once, never update or delete" a privilege-level
guarantee. The row refers to the internal user id, which account closure keeps (NFR-17), and holds
no personal data, so the record of what was accepted survives closure.

**The latest row decides, and `id` decides which is latest.** `id` is an identity column, so it
rises with every insert even inside one transaction, where `accepted_at` could tie. A user whose
latest row names a version other than the current one has not accepted the current one, whether
the current version moved forward or back. `version` is an opaque string compared for equality.

**Indexed on `(user_id, id)`.** The write-path gate reads one user's latest row on every
state-changing request, which this index serves as a backwards scan with no sort.
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

#: A plain string literal, never built from runtime data: `test_sql_parameterisation.py`'s AST
#: guard forbids an f-string as a SQL call's first argument.
_VERSION_NOT_BLANK_SQL = "length(btrim(version)) > 0"


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptance"

    # nptc.audit.policy (NFR-08): every real column classified. `version` is the substance of the
    # event. `user_id` is an internal UUID and not identifying data, as in `user_role.py`, and
    # without it the log could not say whose acceptance this was. `id` is bookkeeping and
    # `accepted_at` repeats the event's own `occurred_at`.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"user_id", "version"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "accepted_at"})

    __table_args__ = (
        CheckConstraint(_VERSION_NOT_BLANK_SQL, name="version_not_blank"),
        Index("ix_terms_acceptance_user_id_id", "user_id", "id"),
    )

    # Identity, not a `serial` default, for the reason `AuditEvent.sequence` gives: INSERT on the
    # table suffices, with no separate `GRANT USAGE ON SEQUENCE`.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    version: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # `clock_timestamp()`, not `now()`: the instant of acceptance, not the start of a transaction.
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
