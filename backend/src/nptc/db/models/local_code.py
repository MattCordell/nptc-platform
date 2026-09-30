"""The `local_code` table: one member of a `local_code_system` (FR-90, FR-92). See PRD SS6.6.

**`code` is `TEXT`, always.** A local code (for example `'chemical_pathology'`) is a string, not a
SNOMED SCTID, but FR-06's discipline for `code_binding.code` applies: a `property_value` row
references it as a stable identifier, so it must never be coerced to a number.

**`provisional` is FR-92's storage answer.** The `Subgroup` sample mixes classification axes
(`Coagulation` and `Drug measurement` classify by analyte; `Microbial Culture`,
`Mycobacteria culture` and `Mycobacterial microscopy` by method) and is inconsistently pluralised.
PRD SS6.6 leaves the real vocabulary to RCPA-QAP and says to migrate the existing strings verbatim
as provisional codes until then. A migrated `Subgroup` string therefore lands with
`provisional = true` and `definition = NULL`, so "not yet reconciled" is a stored fact. `Discipline`
codes seeded from the PRD's verified table (migration 0011) are never provisional.

**Retired via `status`, never deleted**, as `code_binding.status` is. `nptc_app`'s column-level
grant excludes `id`, `system_id` and `code`: rebinding to a different system or code is a new row.
`deprecated_at` and `deprecation_reason` are both mandatory exactly when `status = 'deprecated'` and
forbidden otherwise. `ck_local_code_deprecated_at` makes the timestamp a database invariant rather
than something `nptc.catalogue.local_codes.deprecate_local_code` happens to set. That matters
because FR-90's "version history tied to catalogue releases" is deferred: `deprecated_at` plus the
NFR-08 audit trail reconstructs past state meanwhile.

**What reads this table.** The FR-45 validation sweep's `local_code_retired` warning keys off
`status` here. `nptc.registry.handlers.LocalCodeLookup` is the read path for a property bound to
`binding_target = 'local_code_system'`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = ["LocalCode", "LocalCodeStatus"]


class LocalCodeStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


#: Plain literals, never built from the `StrEnum` above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_CODE_NOT_BLANK_SQL = "length(btrim(code)) > 0"
_DISPLAY_NOT_BLANK_SQL = "length(btrim(display)) > 0"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"
#: Mandatory exactly when deprecated, forbidden while active (as `code_binding.retirement_reason`).
_DEPRECATION_REASON_CHECK_SQL = (
    "(status = 'deprecated') = "
    "(deprecation_reason IS NOT NULL AND length(btrim(deprecation_reason)) > 0)"
)
#: Mandatory exactly when deprecated, forbidden while active. Without the CHECK, only the one write
#: path (`deprecate_local_code`) would guarantee the timestamp.
_DEPRECATED_AT_CHECK_SQL = "(status = 'deprecated') = (deprecated_at IS NOT NULL)"


class LocalCode(Base):
    __tablename__ = "local_code"

    # nptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "system_id",
            "code",
            "display",
            "definition",
            "provisional",
            "status",
            "deprecated_at",
            "deprecation_reason",
            "display_order",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_CODE_NOT_BLANK_SQL, name="code_not_blank"),
        CheckConstraint(_DISPLAY_NOT_BLANK_SQL, name="display_not_blank"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_DEPRECATION_REASON_CHECK_SQL, name="deprecation_reason"),
        CheckConstraint(_DEPRECATED_AT_CHECK_SQL, name="deprecated_at"),
        # Codes are unique within a system, not globally. The index name is explicit because
        # `NAMING_CONVENTION`'s `ix` rule keys off `column_0_label` alone.
        Index(
            "uq_local_code_system_id_code",
            "system_id",
            "code",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_code_system.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    display: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), active_history=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    deprecation_reason: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("system_id")
    def _validate_system_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A local code is retired and re-created under another system, never reparented."""
        if "system_id" in self.__dict__ and self.__dict__["system_id"] is not None:
            raise ImmutableFieldError(
                "LocalCode.system_id is immutable and cannot be reassigned "
                f"from {self.__dict__['system_id']!r} to {value!r}"
            )
        return value

    @validates("code")
    def _validate_code_immutable(self, _key: str, value: str) -> str:
        """A code is deprecated and replaced by a new row, never rebound in place."""
        if "code" in self.__dict__ and self.__dict__["code"] is not None:
            raise ImmutableFieldError(
                f"LocalCode.code is immutable and cannot be reassigned "
                f"from {self.__dict__['code']!r} to {value!r}"
            )
        return value
