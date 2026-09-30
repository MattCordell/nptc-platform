"""The `user_identity` table linking an `app_user` to an OIDC `(iss, sub)` pair (NFR-04).

The FK to `app_user` has no `ON DELETE` clause: users are never deleted (NFR-17), only
pseudonymised, so a cascade or `SET NULL` would have nothing to do. The FK is explicitly indexed
because Postgres does not auto-index foreign keys.

`UniqueConstraint("issuer", "subject")` is named `uq_user_identity_issuer` by `NAMING_CONVENTION`
(`column_0_name`, the first column). That looks like a bug but is the convention's documented
behaviour. Do not rename it to `uq_user_identity_issuer_subject` without changing the convention,
because that would change every other multi-column unique and index name in the schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identity"

    # nptc.audit.policy (NFR-08, NFR-26): only `email_verified` is recorded in full. `subject` is
    # the OIDC `sub`, which NFR-04 says must never escape this table, and `email` is PII; with
    # `issuer` they are withheld (changed-by-name only). The emit sites (identity created on login
    # or auto-link, refreshed on repeat login, deleted on closure) all live in `nptc.auth.identity`.
    # `id`, `user_id` and `linked_at` are ignored: the primary key and the FK are never changed
    # fields once set, and `linked_at` is a server-maintained creation timestamp.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"email_verified"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"issuer", "subject", "email"})
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "user_id", "linked_at"})

    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        # A blank `sub` that matches every other blank `sub` is an auth failure mode worth a
        # constraint.
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
    # `active_history=True` on every audited and withheld column; see `User`.
    issuer: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", active_history=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
