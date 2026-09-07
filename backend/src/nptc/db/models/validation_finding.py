"""The `validation_finding` table: PRD SS6.1's `CatalogueEntry --<
ValidationFinding (open / acknowledged / resolved / superseded)` (issue
#141, FR-18, FR-45, FR-55).

**Minimal, read-only shape - landed ahead of the P3 sweep that will
populate it.** FR-45's validation engine (bulk `$expand` + targeted
`$lookup` against Ontoserver, FR-52) and FR-55's acknowledge/resolve
lifecycle transitions are both P3 (`nptc.validation` is still a
placeholder module). FR-18 needs a real `open`-status row to test its
public indicator against now, so this table lands early with just enough
shape to be seeded and read: no `acknowledged_by_user_id`/reason columns
for a lifecycle nothing can drive yet, no sweep, no acknowledge endpoint.
P3 widens this table (and `nptc.db.roles.GRANT_VALIDATION_FINDING_SQL`,
currently SELECT-only for `nptc_app` - see that constant's own comment)
rather than this issue inventing a lifecycle no code exercises.

**`finding_type` is exactly PRD SS10.1's FR-45 table** - the nine checks
named there by their backtick code (`code_not_found` through
`local_code_retired`). FR-47's dual-edition diff findings (the AU-only,
forecast-inactive and absent-from-both-editions cases) are prose in the
PRD without a named type slug of their own; inventing one here would risk
P3's actual sweep engine needing a different name and forcing a second
migration to fix it, so they are deliberately left out of this CHECK
constraint until FR-47 gives them one.

**`entry_id` NOT NULL, `binding_id` nullable.** FR-45 frames every check as
being against a code binding, and FR-18's indicator itself is titled
"the binding has an open finding" - but the indicator PRD SS6.1 draws is
entry-scoped (`CatalogueEntry --< ValidationFinding`), so `entry_id` is
this table's mandatory anchor and `binding_id` narrows to the specific
binding a check ran against, when there is one.

`designation_collision_acknowledgement.py`'s own module docstring already
says it expects to be subsumed by this table once it lands - that
migration is deliberately not attempted here (see this issue's plan);
`docs/architecture/data-model.md` is updated instead to point at this
table as the general mechanism `designation_collision_acknowledgement`
remains a narrow, purpose-built stand-in for.
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

__all__ = ["ValidationFinding"]

#: Plain string literals, never built from an f-string -
#: `test_sql_parameterisation.py`'s AST guard forbids SQL built from
#: runtime data, matching every other model's own precedent.
_FINDING_TYPE_CHECK_SQL = (
    "finding_type IN ('code_not_found', 'code_inactive', 'fsn_drift', "
    "'preferred_term_drift', 'replacement_available', 'out_of_scope_hierarchy', "
    "'unexpected_semantic_tag', 'binding_violation', 'local_code_retired')"
)
_SEVERITY_CHECK_SQL = "severity IN ('error', 'warning', 'info')"
_STATUS_CHECK_SQL = "status IN ('open', 'acknowledged', 'resolved', 'superseded')"


class ValidationFinding(Base):
    __tablename__ = "validation_finding"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    # All five real columns are auditable - none carries a user reference
    # or any other privacy-sensitive value in this P1 shape (unlike
    # `designation_collision_acknowledgement.acknowledged_by_user_id`,
    # which this table has no equivalent of yet - see the module
    # docstring).
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
        # The only access pattern P1 has: `nptc.catalogue.queries.
        # open_finding_entry_ids` batches `entry_id IN (...) AND status =
        # 'open'` for a page of search/list results - partial, not a plain
        # index on `entry_id`, since every read this table serves in P1
        # carries that same `status = 'open'` predicate.
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
    # Nullable: FR-45's checks are framed as running against a binding, but
    # not every future check need be binding-scoped - see the module
    # docstring.
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
