"""The internal `app_user` table.

Physical name `app_user`, not `"user"`: `user` is a reserved word (an unquoted `FROM user` is a
`current_user` trap), so every literal in `roles.py`, migrations and tests would need quoting for no
benefit. NFR-04 fixes the shape of user identity (never the IdP's `sub`), not the identifier.

`status` is `TEXT` plus `CHECK`, not a native `ENUM`: `ALTER TYPE ... ADD VALUE` cannot run inside a
transaction, and Alembic autogenerate mishandles the create/drop-type pair on downgrade.
`data-model.md` sets the same precedent for `property_definition.status`.

There is no `role` column: it would be a second place a role is granted, and FR-44 requires
permission checks, never role-name checks (see `user_role`).

The `UNIQUE` constraint on `username` relies on Postgres's default `NULLS DISTINCT`. `NULLS NOT
DISTINCT` must never be added, because it would cap the platform at one closed (tombstoned) account.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class User(Base):
    __tablename__ = "app_user"

    # nptc.audit.policy (NFR-08, NFR-26): `status` and `closed_at` are recorded in full on a diff.
    # The three identifying columns are withheld (changed-by-name only, under `REDACTED_KEY`), which
    # makes ADR-0017's `close_account` posture (see ADR-0018) a general policy. `id`, `created_at`
    # and `updated_at` are ignored, not omitted: `policy_for` requires every column in exactly one
    # of auditable, withheld or ignored, so a new unclassified column fails loudly.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status", "closed_at"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset(
        {"username", "display_name", "organisation"}
    )
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        # A plain string literal, never joined from `UserStatus`: `test_sql_parameterisation.py`'s
        # AST guard forbids SQL built from runtime data.
        CheckConstraint(
            "status IN ('active','suspended','closed')",
            name="status",
        ),
        # Makes NFR-17 a database invariant: a row cannot be closed while identifying data remains,
        # and cannot be active without it.
        CheckConstraint(
            "(status = 'closed' AND username IS NULL AND display_name IS NULL "
            "AND organisation IS NULL) "
            "OR (status <> 'closed' AND username IS NOT NULL AND display_name IS NOT NULL)",
            name="tombstone",
        ),
        # Kept separate from the tombstone check above so a violation names
        # the right thing.
        CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)",
            name="closed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__` and `__audit_withheld_fields__`:
    # SQLAlchemy knows an attribute's prior value only if it was loaded before reassignment, so
    # without it `diff_instance`'s `load_history()` would report `before=None` for a real change.
    username: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True, active_history=True
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    organisation: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    # A quoted literal: an unquoted `server_default` string is rendered verbatim as SQL, and
    # `DEFAULT active` is not valid DDL for a text column.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
