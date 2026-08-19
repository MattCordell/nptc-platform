"""The `user_role` table: a granted role, and who granted it (issue #44,
FR-44, FR-01).

**No `role` column on `app_user`.** See that model's own docstring: one
role per user would give no grant provenance and no audit trail, and FR-44
requires permission checks, never role-name checks. A user may hold
several roles at once (the PRD's implicit "Reviewer is a Member" reading -
Section 4.5 says "Adds to Member") - `nptc.auth.permissions.
permissions_for_roles` unions every held role's permissions.

**Revocation is a hard `DELETE`, never a `revoked_at` tombstone.** The
append-only, hash-chained `audit_event` table (issues #36/#37) is already
the permanent, tamper-evident history of every grant and revocation. A
`revoked_at` column would be a second, mutable history that can disagree
with the one that must win - and NFR-17's tombstone posture protects
*identifying personal data*, which a role grant is not.

**Only `UPDATE (granted_at)`, nothing else** (see `nptc.db.roles`) - a
grant is created or removed, never edited, so `user_id`/`role`/
`granted_by_user_id` stay immutable at the privilege level, the same
trick migration 0003 plays with `app_user`'s column-level `UPDATE`.
`granted_at` alone is granted, not zero columns, because Postgres
requires *some* `UPDATE` privilege on a table before `SELECT ... FOR
UPDATE` is permitted at all - confirmed against a real container - and
`nptc.auth.grants.assert_not_last_administrator`'s row lock (FR-01) needs
exactly that. `granted_at` is the one column nothing ever writes to after
insert, so this costs nothing real.

**No separate index on `user_id`.** Unlike `user_identity` (unique on
`(issuer, subject)`, needing its own `ix_user_identity_user_id`), this
table's own `UNIQUE (user_id, role)` already leads with `user_id`, so "what
roles does this user hold" is served by the same index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

#: A plain string literal, never built from `nptc.auth.permissions.Role` -
#: matches `app_user.status`'s own precedent and
#: `backend/tests/test_sql_parameterisation.py`'s AST guard (no SQL built
#: from runtime data). Deliberately omits `'anon'`: ANON is a matrix
#: column, never a grantable row - see `Role.GRANTABLE_ROLES`, and
#: `test_db_user_role_privileges.py`/`test_permissions_data.py` both assert
#: this constraint text and `GRANTABLE_ROLES` agree.
_ROLE_CHECK_SQL = "role IN ('observer','provisional','member','reviewer','administrator')"


class UserRole(Base):
    __tablename__ = "user_role"

    # nptc.audit.policy (issue #37, NFR-08): every column must be
    # classified. Nothing is withheld here - `user_id`/`granted_by_user_id`
    # are internal UUIDs (not the NFR-26/NFR-35 identifying data
    # `app_user.username`/`display_name`/`organisation` withhold) and
    # `role` is a fixed enum value, none of it PII. Withholding `user_id`
    # would make the log unable to say *whose* role changed, defeating the
    # entire point of the event. `id`/`granted_at` are ignored: the primary
    # key is never itself a changed field, and `granted_at` is a
    # server-maintained creation timestamp, not an edit (a grant is never
    # edited - see the module docstring on why there is no `UPDATE`).
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"user_id", "role", "granted_by_user_id"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "granted_at"})

    __table_args__ = (
        UniqueConstraint("user_id", "role"),
        CheckConstraint(_ROLE_CHECK_SQL, name="role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    # `active_history=True` (issue #37) on every audited column - without
    # it, nptc.audit.diffing.diff_instance's load_history() call cannot
    # recover a prior value reassigned on this instance before ever being
    # (re)loaded. See User's/UserIdentity's own comments on the same
    # pattern.
    role: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL only for the one-time bootstrap grant (scripts/grant_role.py,
    # `AuditContext.system()`) - every human-initiated grant sets this.
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True, active_history=True
    )
