"""The `user_role` table: a granted role, and who granted it (FR-44, FR-01).

**No `role` column on `app_user`.** One role per user would give no grant provenance or audit trail,
and FR-44 requires permission checks, never role-name checks. A user may hold several roles (PRD
Section 4.5: Reviewer "Adds to Member"), and `nptc.auth.permissions.permissions_for_roles` unions
every held role's permissions.

**Revocation is a hard `DELETE`, never a `revoked_at` tombstone.** The append-only, hash-chained
`audit_event` table is already the permanent, tamper-evident history of every grant and revocation.
A `revoked_at` column would be a second, mutable history that can disagree with the one that must
win, and NFR-17's tombstone posture protects identifying personal data, which a role grant is not.

**Only `UPDATE (granted_at)`, nothing else** (see `nptc.db.roles`). A grant is created or removed,
never edited, so `user_id`, `role` and `granted_by_user_id` stay immutable at the privilege level,
as in migration 0003's column-level `UPDATE` on `app_user`. `granted_at` is granted rather than no
column because Postgres requires some `UPDATE` privilege on a table before `SELECT ... FOR UPDATE`
is permitted, and the row lock in `nptc.auth.grants.assert_not_last_administrator` (FR-01) needs it.
Nothing writes `granted_at` after insert, so this costs nothing.

**No separate index on `user_id`.** Unlike `user_identity`, this table's `UNIQUE (user_id, role)`
already leads with `user_id`, so "what roles does this user hold" uses that index.
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

#: A plain string literal, never built from `nptc.auth.permissions.Role`
#: (`test_sql_parameterisation.py`'s AST guard). It omits `'anon'`: ANON is a matrix column, never a
#: grantable row (see `Role.GRANTABLE_ROLES`). `test_db_user_role_privileges.py` and
#: `test_permissions_data.py` assert that this text and `GRANTABLE_ROLES` agree.
_ROLE_CHECK_SQL = "role IN ('observer','provisional','member','reviewer','administrator')"


class UserRole(Base):
    __tablename__ = "user_role"

    # nptc.audit.policy (NFR-08): nothing is withheld. `user_id` and `granted_by_user_id` are
    # internal UUIDs, not the identifying data NFR-26 and NFR-35 cover, and `role` is a fixed enum
    # value. Withholding `user_id` would leave the log unable to say whose role changed. `id` and
    # `granted_at` are ignored: the primary key is never a changed field, and `granted_at` is a
    # server-maintained creation timestamp (a grant is never edited).
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
    # `active_history=True` on every audited column; see `User`.
    role: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL only for the one-time bootstrap grant (scripts/grant_role.py,
    # `AuditContext.system()`) - every human-initiated grant sets this.
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True, active_history=True
    )
