"""The `code_binding` table: the terminology server's served labels for a
`catalogue_entry` (issue #48, FR-06, FR-08, FR-82, FR-83). See PRD SS6.4.

**Stored exactly as served, forever (FR-82).** `fsn` and `au_preferred_term`
carry no `@validates` hook at all - unlike `Designation.term`/
`CatalogueEntry.preferred_term`, which both run their value through a
whitespace-cleaning hook before it is ever assigned. That asymmetry is
deliberate: a stored value that has been transformed cannot be
distinguished from one that has not, and that ambiguity is the entire source
of FR-83's tag-stripping hazard - strip `Microscopy (acid fast bacilli)
(procedure)` twice and you silently get `Microscopy`. `docs/adr/0022-
designation-storage.md` is the sibling decision that keeps a served label out
of `designation` for the same reason; this module is the other half of that
decision, where the served labels actually live.

`backend/tests/test_catalogue_bindings.py` pins this module's source as
having no reference to the cleaning helper `nptc.catalogue.term_hygiene`
exposes, nor to the export renderer's semantic-tag strip - a future edit
that starts transforming either column at write time fails a test rather
than passing review unnoticed.

**`code` is `TEXT`, never numeric, and the database itself enforces both
halves of FR-06** - `^[0-9]{6,18}$` and the Verhoeff check digit - via
`nptc_sctid_is_valid` (`nptc.db.functions`, issue #48/ADR-0023). A malformed
or Verhoeff-failing SCTID is rejected at the database layer, not only by
`nptc.catalogue.bindings.create_binding`'s own `SCTID(...)` construction.

**A binding is retired, never deleted (FR-08).** Mirrors `Designation`'s own
"never `DELETE`d, only retired" precedent
(`nptc.db.roles.REVOKE_CODE_BINDING_DELETE_SQL`), with two differences a
designation doesn't need: `retirement_reason` is mandatory exactly when
`status = 'retired'` (`ck_code_binding_retirement_reason` below), and
`replaced_by_binding_id` is populated only in the replacement case PRD FR-08
actually describes ("where a code is being replaced following
inactivation") - a code simply withdrawn with no successor leaves it `NULL`.
`entry_id`, `system` and `code` are excluded from the `UPDATE` grant for the
same reason `business_key` is excluded on `catalogue_entry`: rebinding to a
different concept is a retire-and-replace, never an in-place edit - that is
what makes FR-82's provenance guarantee a privilege-level invariant rather
than an application convention. `fsn`/`au_preferred_term` *are* updatable:
the FR-45 validation sweep must be able to refresh a drifted label from the
server, which is a refresh from the wire, not a re-derivation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql import func

from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError

__all__ = [
    "CodeBinding",
    "CodeBindingEditionHint",
    "CodeBindingStatus",
]

#: PRD SS6.4's default system URI. Not itself a column-level CHECK - PRD SS6.4
#: dropped the speculative `binding_role`/LOINC anticipation, and pinning
#: `system` to one value would be that speculation inverted.
SNOMED_CT_SYSTEM = "http://snomed.info/sct"


class CodeBindingEditionHint(StrEnum):
    AU = "au"
    INTERNATIONAL = "int"
    UNKNOWN = "unknown"


class CodeBindingStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


#: Plain string literals, never built from the `StrEnum`s above - matches
#: `designation.py`'s/`catalogue_entry.py`'s own precedent, enforced by
#: `test_sql_parameterisation.py`'s AST guard.
_SYSTEM_NOT_BLANK_SQL = "length(btrim(system)) > 0"
_FSN_NOT_BLANK_SQL = "length(btrim(fsn)) > 0"
_AU_PREFERRED_TERM_NOT_BLANK_SQL = (
    "au_preferred_term IS NULL OR length(btrim(au_preferred_term)) > 0"
)
_EDITION_HINT_CHECK_SQL = "edition_hint IN ('au','int','unknown')"
_STATUS_CHECK_SQL = "status IN ('active','retired')"
#: FR-08: mandatory exactly when retired, forbidden while active - so a
#: retirement reason can never linger on a binding that becomes active again
#: (bindings are never reactivated, but the constraint costs nothing extra to
#: hold that line too).
_RETIREMENT_REASON_CHECK_SQL = (
    "(status = 'retired') = "
    "(retirement_reason IS NOT NULL AND length(btrim(retirement_reason)) > 0)"
)
_REPLACED_BY_REQUIRES_RETIRED_SQL = "replaced_by_binding_id IS NULL OR status = 'retired'"
_NO_SELF_SUPERSESSION_SQL = "replaced_by_binding_id IS NULL OR replaced_by_binding_id <> id"
#: The database-layer half of FR-06 - see the module docstring and
#: `nptc.db.functions.CREATE_SCTID_VALIDATION_FUNCTION_SQL`.
_CODE_CHECK_SQL = "nptc_sctid_is_valid(code)"


class CodeBinding(Base):
    __tablename__ = "code_binding"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "entry_id",
            "system",
            "code",
            "fsn",
            "au_preferred_term",
            "edition_hint",
            "status",
            "replaced_by_binding_id",
            "retirement_reason",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_SYSTEM_NOT_BLANK_SQL, name="system_not_blank"),
        CheckConstraint(_CODE_CHECK_SQL, name="code"),
        CheckConstraint(_FSN_NOT_BLANK_SQL, name="fsn_not_blank"),
        CheckConstraint(_AU_PREFERRED_TERM_NOT_BLANK_SQL, name="au_preferred_term_not_blank"),
        CheckConstraint(_EDITION_HINT_CHECK_SQL, name="edition_hint"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_RETIREMENT_REASON_CHECK_SQL, name="retirement_reason"),
        CheckConstraint(_REPLACED_BY_REQUIRES_RETIRED_SQL, name="replaced_by_requires_retired"),
        CheckConstraint(_NO_SELF_SUPERSESSION_SQL, name="no_self_supersession"),
        # FR-08: at most one active binding per entry - a partial unique
        # index, explicit name (NAMING_CONVENTION's `ix` rule keys off
        # `column_0_label` alone, matching `designation.py`'s own
        # precedent for why this can't be left to autogeneration).
        Index(
            "ix_code_binding_one_active_per_entry",
            "entry_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column named in __audit_fields__ above
    # (issue #37) - matches `designation.py`'s own precedent.
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    # A plain literal, not built from `SNOMED_CT_SYSTEM` -
    # `test_sql_parameterisation.py`'s AST guard rejects an f-string
    # first argument to `text(...)` even when the interpolated value is a
    # module-level constant, matching `designation.py`'s own
    # `use`/`language`/`status` defaults, which are hand-written literals
    # for the same reason.
    system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'http://snomed.info/sct'"),
        active_history=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # No `@validates` hook on `fsn`/`au_preferred_term` - see module
    # docstring: a served label is stored exactly as served (FR-82), never
    # cleaned, trimmed or otherwise transformed at rest.
    fsn: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    au_preferred_term: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    edition_hint: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'"), active_history=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    replaced_by_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("code_binding.id"),
        nullable=True,
        active_history=True,
    )
    retirement_reason: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("entry_id")
    def _validate_entry_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A binding is retired and replaced by a new row, never
        reparented - matching `Designation._validate_entry_id_immutable`'s
        own guard. `nptc.db.roles.GRANT_CODE_BINDING_UPDATE_SQL`'s column
        exclusion is the actual database invariant; this is the fail-loud
        Python-level layer."""
        if "entry_id" in self.__dict__ and self.__dict__["entry_id"] is not None:
            raise ImmutableFieldError(
                "CodeBinding.entry_id is immutable and cannot be reassigned "
                f"from {self.__dict__['entry_id']!r} to {value!r}"
            )
        return value

    @validates("code")
    def _validate_code_immutable(self, _key: str, value: str) -> str:
        """A binding is retired and replaced by a new row rather than
        rebound in place (FR-82's provenance guarantee) -
        `nptc.db.roles.GRANT_CODE_BINDING_UPDATE_SQL`'s column exclusion is
        the actual database invariant; this is the fail-loud Python-level
        layer, matching `CatalogueEntry._validate_business_key_immutable`."""
        if "code" in self.__dict__ and self.__dict__["code"] is not None:
            raise ImmutableFieldError(
                f"CodeBinding.code is immutable and cannot be reassigned "
                f"from {self.__dict__['code']!r} to {value!r}"
            )
        return value
