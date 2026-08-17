"""The `user_identity` table linking an `app_user` to an OIDC `(iss, sub)`
pair (issue #42, NFR-04).

No `ON DELETE` clause on the FK to `app_user`: users are never deleted
(NFR-17), only pseudonymised, so there is nothing for a cascade or
`SET NULL` to ever do. The FK is explicitly indexed - Postgres does not
auto-index foreign keys the way some other databases do.

`UniqueConstraint("issuer", "subject")` is named `uq_user_identity_issuer`
by `NAMING_CONVENTION` (`column_0_name`, i.e. the *first* listed column) -
this looks like a bug at a glance but is exactly what the naming
convention documents; don't "fix" it into `uq_user_identity_issuer_subject`
without updating the convention itself, since that would change every
other multi-column unique/index name in the schema too.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        # A blank `sub` that silently matches every other blank `sub` is the
        # auth failure mode worth a constraint, not just a code review note.
        CheckConstraint("length(btrim(issuer)) > 0", name="issuer_not_blank"),
        CheckConstraint("length(btrim(subject)) > 0", name="subject_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, index=True
    )
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
