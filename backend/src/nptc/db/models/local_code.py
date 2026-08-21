"""The `local_code` table: one member of a `local_code_system` (issue #56,
FR-90, FR-92). See PRD SS6.6.

**`code` is `TEXT`, always** - a local code (e.g. `'chemical_pathology'`)
is a string, not a SNOMED SCTID, but the same discipline FR-06 requires of
`code_binding.code` applies here for the same reason: this is a stable
identifier a `property_value` row will reference, and it must never be
coerced to a number.

**`provisional` is FR-92's storage answer.** The `Subgroup` sample mixes
classification axes (`Coagulation`/`Drug measurement` classify by analyte,
`Microbial Culture`/`Mycobacteria culture`/`Mycobacterial microscopy`
classify by method) and is inconsistently pluralised - PRD SS6.6 is
explicit that RCPA-QAP decides the real vocabulary, and "migrate the
existing strings verbatim as provisional codes" until then. A migrated
`Subgroup` string therefore lands with `provisional = true` and
`definition = NULL`: "not yet reconciled" is a stored fact, not an
absence a reader has to infer. `Discipline` codes seeded from the PRD's
own verified table (migration 0010) are never provisional.

**Retired via `status`, never deleted** - mirrors `code_binding.status`'s
own treatment; `nptc_app`'s column-level grant excludes `id`, `system_id`
and `code` for the same reason `code_binding.entry_id`/`code` are
excluded (rebinding to a different system or code is a new row, not an
edit). `deprecated_at`/`deprecation_reason` follow `property_definition`'s
and `code_binding.retirement_reason`'s own precedent respectively: **both**
mandatory exactly when `status = 'deprecated'`, forbidden otherwise -
`ck_local_code_deprecated_at`'s `(status = 'deprecated') = (deprecated_at
IS NOT NULL)` makes the timestamp a real database invariant rather than a
value the one write path (`nptc.catalogue.local_codes.deprecate_local_code`)
merely happens to set. This matters beyond the row itself: FR-90's
"version history tied to catalogue releases" (its own third bullet) is
deferred to a follow-up issue precisely because `deprecated_at` plus the
NFR-08 audit trail are what make past state reconstructable in the
meantime - a table where that timestamp could be silently skipped would
undercut the argument used to defer.

**What reads this table.** The FR-45 validation sweep's `local_code_retired`
warning (PRD line 689: "Do local code system values still resolve to an
active local code?") keys off `status` here; `nptc.registry.handlers.
LocalCodeLookup` is the read path once #53 wires a `code`-datatype property
bound to `binding_target = 'local_code_system'`."""

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


#: Plain string literals, never built from the `StrEnum` above - matches
#: `code_binding.py`'s own precedent, enforced by
#: `test_sql_parameterisation.py`'s AST guard.
_CODE_NOT_BLANK_SQL = "length(btrim(code)) > 0"
_DISPLAY_NOT_BLANK_SQL = "length(btrim(display)) > 0"
_STATUS_CHECK_SQL = "status IN ('active','deprecated')"
#: FR-90/code_binding.retirement_reason precedent: mandatory exactly when
#: deprecated, forbidden while active.
_DEPRECATION_REASON_CHECK_SQL = (
    "(status = 'deprecated') = "
    "(deprecation_reason IS NOT NULL AND length(btrim(deprecation_reason)) > 0)"
)
#: Mandatory exactly when deprecated, forbidden while active - tightened
#: from an earlier "may be set only when deprecated" reading after review
#: (the one write path setting it is not itself a database invariant
#: without this). Mirrors `_DEPRECATION_REASON_CHECK_SQL`'s own shape.
_DEPRECATED_AT_CHECK_SQL = "(status = 'deprecated') = (deprecated_at IS NOT NULL)"


class LocalCode(Base):
    __tablename__ = "local_code"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
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
        # Codes are unique within a system, not globally - `ix` names off
        # `column_0_label` alone (`NAMING_CONVENTION`), so this composite
        # index needs an explicit name, matching `code_binding.py`'s own
        # precedent for the same reason.
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
        """A local code is retired and re-created under a different
        system, never reparented - matches `CodeBinding.
        _validate_entry_id_immutable`'s own guard."""
        if "system_id" in self.__dict__ and self.__dict__["system_id"] is not None:
            raise ImmutableFieldError(
                "LocalCode.system_id is immutable and cannot be reassigned "
                f"from {self.__dict__['system_id']!r} to {value!r}"
            )
        return value

    @validates("code")
    def _validate_code_immutable(self, _key: str, value: str) -> str:
        """Mirrors `CodeBinding._validate_code_immutable`: a code is
        deprecated and replaced by a new row, never rebound in place."""
        if "code" in self.__dict__ and self.__dict__["code"] is not None:
            raise ImmutableFieldError(
                f"LocalCode.code is immutable and cannot be reassigned "
                f"from {self.__dict__['code']!r} to {value!r}"
            )
        return value
