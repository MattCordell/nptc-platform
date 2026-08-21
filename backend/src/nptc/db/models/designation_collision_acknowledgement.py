"""The `designation_collision_acknowledgement` table: FR-05's warning-
severity acknowledgement (issue #49). See PRD SS6.3.

**Not the FR-55 `ValidationFinding` lifecycle.** PRD SS6.1 draws
`CatalogueEntry --< ValidationFinding (open / acknowledged / resolved /
superseded)` as the general acknowledgement mechanism for every
terminology-validation finding (FR-45's sweep, FR-84's subsumption check,
and so on) - but that entity is P3 (`nptc.validation` is still a
placeholder module) and this table's own dependency, FR-05 collision
detection, is P1. This table is a narrow, purpose-built acknowledgement for
exactly one finding shape - the same synonym on multiple live entries -
scoped so P1 does not have to wait on P3's general machinery. When
`ValidationFinding` lands, it is expected to subsume this table rather than
sit alongside it; that migration is deliberately not attempted here.

**Scope: (entry, term_key, language), not (term_key, language) alone.** An
acknowledgement silences the warning for the specific entry it was made
against - a fourth entry later joining an already-acknowledged group (PRD
A.5's `'ADA2'`) still warns once, on its own save, rather than inheriting
someone else's earlier decision. `nptc.catalogue.collisions.
warning_collisions` is what reads this table.

**Never edited or withdrawn.** An acknowledgement is a record of an
editorial decision at a point in time - `nptc.db.roles.
REVOKE_DESIGNATION_COLLISION_ACK_WRITE_SQL` makes "insert once, never
update or delete" a privilege-level guarantee, the same trick every other
`retired`/append-only table in this schema already uses. Withdrawing an
acknowledgement (so the warning resurfaces) is out of scope for #49 and
belongs with FR-55's fuller lifecycle.
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

#: Plain string literals, never built from runtime data -
#: `test_sql_parameterisation.py`'s AST guard forbids SQL built from an
#: f-string, matching every other model's own precedent.
_TERM_KEY_NOT_BLANK_SQL = "length(btrim(term_key)) > 0"
_REASON_NOT_BLANK_SQL = "length(btrim(reason)) > 0"
#: Built from `LANGUAGE_TAG_PATTERN.pattern` rather than hand-copied, so
#: this can never silently diverge from `designation.py`'s own check -
#: matching that model's own `_LANGUAGE_CHECK_SQL` precedent.
_LANGUAGE_CHECK_SQL = f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'"


class DesignationCollisionAcknowledgement(Base):
    __tablename__ = "designation_collision_acknowledgement"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    # `reason` is auditable (it is the substance of the editorial decision
    # FR-05 requires); `acknowledged_by_user_id` is withheld (changed-by-name
    # only, matching `user_identity.py`'s own precedent for a user
    # reference that must not appear verbatim in a diff, NFR-04/NFR-26).
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
        # One acknowledgement per (entry, term_key, language) - a second
        # attempt is a no-op at the service layer, matching
        # `DesignationAlreadyRetiredError`'s own "don't silently write a
        # second no-change event" posture.
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
    # Nullable: `AuditContext.system()`-attributed acknowledgements (a
    # seeded/backfilled decision with no human actor) have none - matching
    # `AuditEvent.actor_user_id`'s own nullable treatment.
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
