"""The `validation_finding` table (FR-18, FR-45, FR-55):
`CatalogueEntry --< ValidationFinding (open / acknowledged / resolved / superseded)` in PRD SS6.1.

**Minimal and read-only, landed ahead of the P3 sweep that will populate it.** FR-45's validation
engine (FR-52) and FR-55's acknowledge and resolve transitions are P3, and `nptc.validation` is
still a placeholder. FR-18 needs a real `open` row to test its public indicator against, so the
table has just enough shape to be seeded and read: no `acknowledged_by_user_id` or reason columns
for a lifecycle nothing can drive yet. P3 widens this table and
`nptc.db.roles.GRANT_VALIDATION_FINDING_SQL` (SELECT-only for `nptc_app` today).

**`finding_type` is exactly PRD SS10.1's FR-45 table**: the nine checks named there by their
backtick code (`code_not_found` through `local_code_retired`). FR-47's dual-edition diff findings
have no named type slug in the PRD. Inventing one risks P3's sweep needing a different name and a
second migration, so they stay out of this CHECK until FR-47 names them.

**`entry_id` NOT NULL, `binding_id` nullable.** FR-45 frames every check as against a code binding,
but the indicator PRD SS6.1 draws is entry-scoped. So `entry_id` is the mandatory anchor, and
`binding_id` narrows to the binding a check ran against, when there is one.

`designation_collision_acknowledgement` is a narrow stand-in that this table is expected to subsume
once its lifecycle lands; that migration is not attempted yet. `docs/architecture/data-model.md`
records this table as the general mechanism.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["ValidationFinding", "ValidationFindingStatus"]


class ValidationFindingStatus(StrEnum):
    """FR-55's four-state lifecycle. `finding_type` and `severity` get no enum yet: nothing in P1
    branches on them in Python (only the CHECK fixes their values), unlike `status`, which
    `nptc.catalogue.queries.open_finding_business_keys` filters on. P3's sweep will likely want one
    for `finding_type`.
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


#: Plain string literals, never built from an f-string: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_FINDING_TYPE_CHECK_SQL = (
    "finding_type IN ('code_not_found', 'code_inactive', 'fsn_drift', "
    "'preferred_term_drift', 'replacement_available', 'out_of_scope_hierarchy', "
    "'unexpected_semantic_tag', 'binding_violation', 'local_code_retired')"
)
_SEVERITY_CHECK_SQL = "severity IN ('error', 'warning', 'info')"
_STATUS_CHECK_SQL = "status IN ('open', 'acknowledged', 'resolved', 'superseded')"


class ValidationFinding(Base):
    __tablename__ = "validation_finding"

    # nptc.audit.policy (NFR-08): every real column classified. All five are auditable: none carries
    # a user reference or privacy-sensitive value in this P1 shape.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"entry_id", "binding_id", "finding_type", "severity", "status"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_FINDING_TYPE_CHECK_SQL, name="finding_type"),
        CheckConstraint(_SEVERITY_CHECK_SQL, name="severity"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        # The only P1 access pattern: `nptc.catalogue.queries.open_finding_business_keys` joins on
        # `entry_id` and `status = 'open'`. Partial rather than a plain `entry_id` index because
        # every P1 read carries that predicate.
        Index(
            "ix_validation_finding_open_entry_id",
            "entry_id",
            postgresql_where=text("status = 'open'"),
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
        active_history=True,
    )
    # Nullable: not every future check need be binding-scoped; see the module docstring.
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("code_binding.id"),
        nullable=True,
        active_history=True,
    )
    finding_type: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
