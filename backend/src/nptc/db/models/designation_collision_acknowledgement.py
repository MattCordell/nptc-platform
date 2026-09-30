"""The `designation_collision_acknowledgement` table: FR-05's warning-severity acknowledgement. See
PRD SS6.3.

**Not the FR-55 `ValidationFinding` lifecycle**
(`nptc.db.models.validation_finding.ValidationFinding`). PRD SS6.1 draws `ValidationFinding` as the
general acknowledgement mechanism for every terminology-validation finding. It is still read-only,
because the P3 sweep and acknowledge/resolve endpoints have not landed, while FR-05 collision
detection was P1. This table is a narrow acknowledgement for one finding shape, the same synonym on
multiple live entries, so P1 did not wait on P3. `ValidationFinding` is expected to subsume it once
its lifecycle lands; `docs/architecture/data-model.md` records that, and the migration is
not attempted yet.

**Scope: (entry, term_key, language), not (term_key, language) alone.** An acknowledgement silences
the warning for the entry it was made against. A fourth entry joining an acknowledged group (PRD
A.5's `'ADA2'`) still warns once, on its own save. `nptc.catalogue.collisions.warning_collisions`
reads this table.

**Never edited or withdrawn.** An acknowledgement records an editorial decision at a point in time.
`nptc.db.roles.REVOKE_DESIGNATION_COLLISION_ACK_WRITE_SQL` makes "insert once, never update or
delete" a privilege-level guarantee. Withdrawing one, so the warning resurfaces, belongs with
FR-55's fuller lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc_shared.language import LANGUAGE_TAG_PATTERN

__all__ = ["DesignationCollisionAcknowledgement"]

#: Plain string literals, not built from runtime data: `test_sql_parameterisation.py`'s AST guard
#: forbids an f-string as a SQL call's first argument.
_TERM_KEY_NOT_BLANK_SQL = "length(btrim(term_key)) > 0"
_REASON_NOT_BLANK_SQL = "length(btrim(reason)) > 0"
#: Built from `LANGUAGE_TAG_PATTERN.pattern` so it cannot diverge from `designation.py`'s check.
_LANGUAGE_CHECK_SQL = f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'"


class DesignationCollisionAcknowledgement(Base):
    __tablename__ = "designation_collision_acknowledgement"

    # nptc.audit.policy (NFR-08): every real column classified. `reason` is auditable: it is the
    # substance of the editorial decision FR-05 requires. `acknowledged_by_user_id` is withheld
    # (changed-by-name only, as in `user_identity.py`): a user reference must not appear verbatim in
    # a diff (NFR-04, NFR-26).
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"entry_id", "term_key", "language", "reason"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"acknowledged_by_user_id"})
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_TERM_KEY_NOT_BLANK_SQL, name="term_key_not_blank"),
        CheckConstraint(_REASON_NOT_BLANK_SQL, name="reason_not_blank"),
        CheckConstraint(_LANGUAGE_CHECK_SQL, name="language"),
        # One acknowledgement per (entry, term_key, language). A second attempt is a no-op at the
        # service layer (`nptc.catalogue.collisions.acknowledge_collision` selects first and returns
        # the existing row), as in `nptc.auth.grants.grant_role`'s no-op for a role already held:
        # re-acknowledging is not a caller error worth surfacing.
        Index(
            "ix_designation_collision_ack_entry_term_language",
            "entry_id",
            "term_key",
            "language",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    term_key: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    language: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'en-AU'"), active_history=True
    )
    # Nullable: an `AuditContext.system()` acknowledgement (a seeded or backfilled decision) has no
    # human actor, as with `AuditEvent.actor_user_id`.
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id"),
        nullable=True,
        active_history=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
