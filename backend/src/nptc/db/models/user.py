"""The internal `app_user` table (issue #42).

Physical name `app_user`, not `"user"`: the latter is a reserved word (and
an unquoted `FROM user` is a `current_user` trap), so every literal in
`roles.py`, migrations and tests would need quoting for no benefit -
NFR-04 fixes the *shape* of user identity (never the IdP's `sub`), not the
identifier chosen for it.

`status` is `TEXT` + `CHECK`, not a native `ENUM`: `ALTER TYPE ... ADD
VALUE` cannot run inside a transaction and Alembic autogenerate mishandles
the create/drop-type pair on downgrade - `data-model.md` already sets this
precedent for `property_definition.status`.

No `role` column: adding one here would create a second place a role is
granted, and FR-44 requires permission checks, never role-name checks.
Role grants land with #44.

The `UNIQUE` constraint on `username` relies on Postgres's default
`NULLS DISTINCT` behaviour - `NULLS NOT DISTINCT` must never be added to
it, since that would cap the platform at exactly one closed (tombstoned)
account system-wide.
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

    # nptc.audit.policy (issue #37, NFR-08/NFR-26): status/closed_at are
    # recorded in full on a diff; the three identifying columns are
    # withheld (changed-by-name only, under REDACTED_KEY) - this turns
    # ADR-0017's close_account posture (see docs/adr/0018-*.md) into a
    # general policy rather than one function's own care. No `id` here:
    # the primary key is never itself a "changed field".
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status", "closed_at"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset(
        {"username", "display_name", "organisation"}
    )

    __table_args__ = (
        # Constraint text is a plain string literal, never f-string joined
        # from UserStatus - backend/tests/test_sql_parameterisation.py's AST
        # guard forbids building SQL from runtime data, and there is no
        # runtime data here to justify the risk in the first place.
        CheckConstraint(
            "status IN ('active','suspended','closed')",
            name="status",
        ),
        # This is what makes NFR-17 a database invariant, not an
        # application convention: a row cannot be marked closed while any
        # identifying data remains on it, and cannot be active without one.
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
    # `active_history=True` on every column named in __audit_fields__/
    # __audit_withheld_fields__ above (issue #37): without it, SQLAlchemy
    # only knows an attribute's *prior* value if it was already loaded
    # before being reassigned - reassigning an expired-but-unread attribute
    # leaves nptc.audit.diffing.diff_instance's load_history() call with no
    # committed value to report, silently turning a real change into
    # before=None instead of the true prior value.
    username: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True, active_history=True
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    organisation: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    # A quoted literal, not the bare `UserStatus.ACTIVE` value: an
    # unquoted server_default string is rendered verbatim as SQL, and
    # `DEFAULT active` (no quotes) is not valid DDL for a text column.
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
