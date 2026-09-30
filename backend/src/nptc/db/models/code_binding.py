"""The `code_binding` table: the terminology server's served labels for a `catalogue_entry` (FR-06,
FR-08, FR-82, FR-83). See PRD SS6.4.

**Stored exactly as served (FR-82).** `fsn` and `au_preferred_term` have no `@validates` hook,
unlike `Designation.term` and `CatalogueEntry.preferred_term`. A transformed value cannot be told
from an untransformed one, which is the ambiguity behind FR-83's tag-stripping hazard: stripping
`Microscopy (acid fast bacilli) (procedure)` twice silently gives `Microscopy`. ADR-0022 keeps
served labels out of `designation` for the same reason. `backend/tests/test_catalogue_bindings.py`
fails if this module references the term-cleaning helper or the export renderer's semantic-tag
strip.

**`code` is `TEXT`, never numeric, and the database enforces both halves of FR-06** (`^[0-9]{6,18}$`
and the Verhoeff check digit) through `nptc_sctid_is_valid` (`nptc.db.functions`, ADR-0023), not
only through `nptc.catalogue.bindings.create_binding`.

**A binding is retired, never deleted (FR-08).** `nptc.db.roles.REVOKE_CODE_BINDING_DELETE_SQL`
enforces this, as `REVOKE_DESIGNATION_DELETE_SQL` does for `Designation`, with two differences.
`retirement_reason` is mandatory exactly when `status = 'retired'`. `replaced_by_binding_id` is set
only when a code is replaced; a withdrawn code leaves it `NULL`. `entry_id`, `system` and `code` are
excluded from the `UPDATE` grant, so rebinding to a different concept is a retire-and-replace and
FR-82's provenance is a privilege-level invariant. `fsn` and `au_preferred_term` stay updatable so
the FR-45 validation sweep can refresh a drifted label.

**One active binding per code (FR-08).** `ix_code_binding_one_active_per_entry` stops one entry
holding two active bindings. `ix_code_binding_one_active_entry_per_code` stops one code being active
on two entries. Both are partial on `status = 'active'`, so a retired code is immediately
rebindable. `create_binding`'s pre-insert check only turns the violation into a domain error.

**`retired_at` (FR-17).** Mandatory exactly when `status = 'retired'`. It orders a multi-way retired
collision (`retired_at DESC, business_key ASC`, ADR-0033), because two entries can each hold the
same code as a retired binding. `nptc.catalogue.bindings.retire_binding` sets it once. It is
`__audit_ignored_fields__`, like `created_at`: the audit event's own timestamp records when the
retirement happened.
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

#: PRD SS6.4's default system URI. Not a column CHECK: PRD SS6.4 dropped the speculative
#: `binding_role`/LOINC anticipation, and pinning `system` to one value would invert that.
SNOMED_CT_SYSTEM = "http://snomed.info/sct"


class CodeBindingEditionHint(StrEnum):
    AU = "au"
    INTERNATIONAL = "int"
    UNKNOWN = "unknown"


class CodeBindingStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


#: Plain literals, never built from the `StrEnum`s above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_SYSTEM_NOT_BLANK_SQL = "length(btrim(system)) > 0"
_FSN_NOT_BLANK_SQL = "length(btrim(fsn)) > 0"
_AU_PREFERRED_TERM_NOT_BLANK_SQL = (
    "au_preferred_term IS NULL OR length(btrim(au_preferred_term)) > 0"
)
_EDITION_HINT_CHECK_SQL = "edition_hint IN ('au','int','unknown')"
_STATUS_CHECK_SQL = "status IN ('active','retired')"
#: FR-08: mandatory exactly when retired, forbidden while active.
_RETIREMENT_REASON_CHECK_SQL = (
    "(status = 'retired') = "
    "(retirement_reason IS NOT NULL AND length(btrim(retirement_reason)) > 0)"
)
_REPLACED_BY_REQUIRES_RETIRED_SQL = "replaced_by_binding_id IS NULL OR status = 'retired'"
_NO_SELF_SUPERSESSION_SQL = "replaced_by_binding_id IS NULL OR replaced_by_binding_id <> id"
#: FR-17: mandatory exactly when retired, forbidden while active. A real timestamp to order retired
#: collisions by, because `updated_at` moves on any column update.
_RETIRED_AT_CHECK_SQL = "(status = 'retired') = (retired_at IS NOT NULL)"
#: The database half of FR-06; see the module docstring.
_CODE_CHECK_SQL = "nptc_sctid_is_valid(code)"


class CodeBinding(Base):
    __tablename__ = "code_binding"

    # nptc.audit.policy (NFR-08): every real column classified.
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
    # Bookkeeping, not an independent business fact; see the module docstring.
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at", "retired_at"}
    )

    __table_args__ = (
        CheckConstraint(_SYSTEM_NOT_BLANK_SQL, name="system_not_blank"),
        CheckConstraint(_CODE_CHECK_SQL, name="code"),
        CheckConstraint(_FSN_NOT_BLANK_SQL, name="fsn_not_blank"),
        CheckConstraint(_AU_PREFERRED_TERM_NOT_BLANK_SQL, name="au_preferred_term_not_blank"),
        CheckConstraint(_EDITION_HINT_CHECK_SQL, name="edition_hint"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_RETIREMENT_REASON_CHECK_SQL, name="retirement_reason"),
        CheckConstraint(_RETIRED_AT_CHECK_SQL, name="retired_at"),
        CheckConstraint(_REPLACED_BY_REQUIRES_RETIRED_SQL, name="replaced_by_requires_retired"),
        CheckConstraint(_NO_SELF_SUPERSESSION_SQL, name="no_self_supersession"),
        # FR-08: at most one active binding per entry. The name is explicit because
        # NAMING_CONVENTION's `ix` rule keys off `column_0_label` alone (as in `designation.py`).
        Index(
            "ix_code_binding_one_active_per_entry",
            "entry_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # FR-08: at most one active binding per code, across every entry.
        Index(
            "ix_code_binding_one_active_entry_per_code",
            "system",
            "code",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # FR-17: `nptc.catalogue.queries.get_entry_by_code` must see retired rows too, so its query
        # carries no `status` predicate and the planner cannot use the partial indexes above.
        # Non-partial and non-unique because two entries can share a retired binding on one code
        # (ADR-0033); `test_db_code_binding_index_plan.py` proves the index scan.
        Index("ix_code_binding_system_code", "system", "code"),
        # FR-14: this table's contribution to the single search box. All five indexes below are
        # partial on `status = 'active'`, like `ix_designation_term_trgm`: a retired binding is
        # history and search never returns it. `_SEARCH_SQL` spells the predicate as the literal
        # `'active'` so the planner can prove the indexes cover the query.
        #
        # The code is matched by equality, so it gets a btree; ADR-0029 rejects trigram over digits.
        # `ix_code_binding_one_active_entry_per_code` cannot serve the lookup, because `code` is its
        # second column and the search box has no `system` to lead with.
        Index(
            "ix_code_binding_code",
            "code",
            postgresql_where=text("status = 'active'"),
        ),
        # The two stored labels, each indexed both ways (trigram and full-text; see
        # `CatalogueEntry`). Both are indexed tag-intact, as stored (FR-82), so an FSN searched with
        # or without its semantic tag reaches the entry. ADR-0029 rejects a SQL-side tag stripper.
        Index(
            "ix_code_binding_fsn_trgm",
            text("nptc_search_text(fsn)"),
            postgresql_using="gin",
            postgresql_ops={"nptc_search_text(fsn)": "gin_trgm_ops"},
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_code_binding_fsn_fts",
            text("nptc_search_document(fsn)"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
        # `au_preferred_term` is nullable and `nptc_search_text`/`nptc_search_document` are
        # `STRICT`, so a binding without one indexes as NULL and is unfindable through this column.
        # That is correct, so the strictness is a correctness property.
        Index(
            "ix_code_binding_au_preferred_term_trgm",
            text("nptc_search_text(au_preferred_term)"),
            postgresql_using="gin",
            postgresql_ops={"nptc_search_text(au_preferred_term)": "gin_trgm_ops"},
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_code_binding_au_preferred_term_fts",
            text("nptc_search_document(au_preferred_term)"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__`, as in `designation.py`.
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    # A plain literal, not built from `SNOMED_CT_SYSTEM`: `test_sql_parameterisation.py`'s AST guard
    # rejects an f-string first argument to `text(...)`.
    system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'http://snomed.info/sct'"),
        active_history=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # No `@validates` hook: a served label is stored as served (FR-82); see the module docstring.
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
    #: No `active_history=True`: it matters only for auditable or withheld fields, and this column
    #: is `__audit_ignored_fields__`.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("entry_id")
    def _validate_entry_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A binding is retired and replaced, never reparented.
        `nptc.db.roles.GRANT_CODE_BINDING_UPDATE_SQL`'s column exclusion is the database invariant;
        this is the fail-loud Python layer.
        """
        if "entry_id" in self.__dict__ and self.__dict__["entry_id"] is not None:
            raise ImmutableFieldError(
                "CodeBinding.entry_id is immutable and cannot be reassigned "
                f"from {self.__dict__['entry_id']!r} to {value!r}"
            )
        return value

    @validates("code")
    def _validate_code_immutable(self, _key: str, value: str) -> str:
        """A binding is retired and replaced, never rebound in place (FR-82's provenance guarantee).
        `nptc.db.roles.GRANT_CODE_BINDING_UPDATE_SQL`'s column exclusion is the database invariant;
        this is the fail-loud Python layer.
        """
        if "code" in self.__dict__ and self.__dict__["code"] is not None:
            raise ImmutableFieldError(
                f"CodeBinding.code is immutable and cannot be reassigned "
                f"from {self.__dict__['code']!r} to {value!r}"
            )
        return value
