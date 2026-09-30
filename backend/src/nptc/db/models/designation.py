"""The `designation` table: catalogue-side preferred and synonym designations (FR-04, FR-24, FR-37,
FR-85). See PRD §6.3.

**Catalogue-side only: it never stores a SNOMED CT-served label (ADR-0022).** Three
preferred-term-shaped strings live in three places:

- The RCPA/catalogue preferred term is `catalogue_entry.preferred_term`. Users maintain it, and it
  exists before any code binding.
- The SNOMED CT-AU preferred term is `code_binding.au_preferred_term`, stored as served (FR-82) and
  never editable.
- The SNOMED CT Fully Specified Name is `code_binding.fsn`, as served with its semantic tag, and
  never editable.

So `designation` holds only catalogue-authored synonyms and non-en-AU preferred variants. Copying a
served label here would make it editable and break FR-82. The catalogue's en-AU preferred term is
never duplicated here; `_NO_EN_AU_PREFERRED_CHECK_SQL` enforces that.

**`length` has no column.** FR-85's `Length` is the character count of the catalogue's preferred
term (PRD §6.5; see `CatalogueEntry.length`). `Designation.length` applies the same computation to a
designation's `term` but is not the published FR-85 figure. Neither gets a column, because even one
nothing writes to would leave a seam for a migration to populate. Both are `@property`s over
`nptc.catalogue.term_hygiene.preferred_term_length`, with no setter.

**Never `DELETE`d, only retired.** A designation moves to `status='retired'`, as
`CatalogueEntryStatus.WITHDRAWN` does. `nptc.db.roles.REVOKE_DESIGNATION_DELETE_SQL` makes this a
privilege-level guarantee.

**`retired_at`.** As `code_binding.retired_at` (FR-17): mandatory when `status = 'retired'`,
forbidden otherwise (`_RETIRED_AT_CHECK_SQL`), set by `retire_designation` and cleared by
`reinstate_designation`. It orders retired rows that share one `entry_id`, `term_key` and `language`
(`retired_at DESC`, then `id ASC`). It is `__audit_ignored_fields__`, as on `code_binding`.

**`term_key` is FR-05's comparison form, stored and indexed.** The `@validates("term")` hook that
cleans the term also derives `term_key` with `nptc_shared.similarity.collision_key`: casefolded,
with punctuation and whitespace folded to a separator, which is stronger than `clean_term`'s
whitespace fold. It is stored rather than a `@property` because `nptc.catalogue.collisions` needs an
indexed equality lookup across every entry's designations. It is never meaningful on its own, so it
is `__audit_ignored_fields__`.
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

from nptc.catalogue.term_hygiene import (
    DesignationLanguageError,
    TermCleaningError,
    clean_term,
    preferred_term_length,
    validate_language_tag,
)
from nptc.db.base import Base
from nptc.db.models.catalogue_entry import ImmutableFieldError
from nptc_shared.language import LANGUAGE_TAG_PATTERN
from nptc_shared.similarity import collision_key

__all__ = [
    "Designation",
    "DesignationLanguageError",
    "DesignationStatus",
    "DesignationUse",
    "TermCleaningError",
]


class DesignationUse(StrEnum):
    PREFERRED = "preferred"
    SYNONYM = "synonym"


class DesignationStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


#: Plain literals, never built from the `StrEnum`s above: `test_sql_parameterisation.py`'s AST guard
#: forbids SQL built from runtime data.
_USE_CHECK_SQL = "use IN ('preferred','synonym')"
_STATUS_CHECK_SQL = "status IN ('active','retired')"
#: A blank term would silently match every other blank term.
_TERM_NOT_BLANK_SQL = "length(btrim(term)) > 0"
#: BCP-47 well-formedness at the database layer too, so a row inserted outside the
#: `@validates("language")` hook (a future bulk load) cannot carry a malformed tag. Built from
#: `LANGUAGE_TAG_PATTERN.pattern` so the two cannot diverge;
#: `test_designation_language_check_agrees_with_the_shared_pattern` pins it.
_LANGUAGE_CHECK_SQL = f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'"
#: The database half of "the en-AU preferred term lives only in `catalogue_entry.preferred_term`". A
#: non-en-AU preferred variant is still permitted.
_NO_EN_AU_PREFERRED_CHECK_SQL = "NOT (use = 'preferred' AND language = 'en-AU')"
#: Mandatory exactly when retired, forbidden otherwise; mirrors
#: `code_binding._RETIRED_AT_CHECK_SQL`.
_RETIRED_AT_CHECK_SQL = "(status = 'retired') = (retired_at IS NOT NULL)"


class Designation(Base):
    __tablename__ = "designation"

    # nptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"entry_id", "term", "use", "language", "status"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at", "term_key", "retired_at"}
    )

    __table_args__ = (
        CheckConstraint(_USE_CHECK_SQL, name="use"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_TERM_NOT_BLANK_SQL, name="term_not_blank"),
        CheckConstraint(_LANGUAGE_CHECK_SQL, name="language"),
        CheckConstraint(_NO_EN_AU_PREFERRED_CHECK_SQL, name="no_en_au_preferred"),
        CheckConstraint(_RETIRED_AT_CHECK_SQL, name="retired_at"),
        # Explicit names: NAMING_CONVENTION's `ix` rule keys off `column_0_label` alone, so two
        # partial indexes leading with `entry_id` would autogenerate the same name.
        Index(
            "ix_designation_one_active_preferred_per_entry_language",
            "entry_id",
            "language",
            unique=True,
            postgresql_where=text("status = 'active' AND use = 'preferred'"),
        ),
        # No duplicate active (entry_id, term_key, language): the same synonym attached twice
        # (doubled delimiter, whitespace, case or punctuation variant; PRD Appendix A.4) collapses
        # to one row. Keyed on `term_key` so surface forms that fold together count as one synonym,
        # matching `add_synonyms`'s dedup.
        Index(
            "ix_designation_no_duplicate_active_term",
            "entry_id",
            "term_key",
            "language",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # FR-05: an indexed lookup for cross-entry collisions. `nptc.catalogue.collisions` filters
        # status in the query, so a plain btree suffices.
        Index("ix_designation_term_key", "term_key"),
        # FR-14/FR-15: the synonym half of public search (see `CatalogueEntry`'s trigram index for
        # why it is declared in the model and in a migration). Partial on `status = 'active'`,
        # unlike the entry-side index: a retired synonym is history and search never matches it.
        Index(
            "ix_designation_term_trgm",
            text("nptc_search_text(term)"),
            postgresql_using="gin",
            postgresql_ops={"nptc_search_text(term)": "gin_trgm_ops"},
            postgresql_where=text("status = 'active'"),
        ),
        # FR-14/FR-15: the full-text half over the same rows; partial for the same reason.
        Index(
            "ix_designation_term_fts",
            text("nptc_search_document(term)"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__`: without it `diff_instance`'s
    # `load_history()` cannot recover a prior value reassigned before the instance was loaded.
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    term: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # FR-05: derived from `term` by the `@validates` hook below, never assigned directly.
    # `server_default=''` exists only so a raw INSERT that bypasses the ORM still satisfies `NOT
    # NULL`; every ORM write supplies the computed value.
    term_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # Plain literals, not built from the `StrEnum`s above; see `_USE_CHECK_SQL`.
    use: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'synonym'"), active_history=True
    )
    language: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'en-AU'"), active_history=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    #: Mirrors `code_binding.retired_at` (FR-17); see the module docstring.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("entry_id")
    def _validate_entry_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A designation is retired and re-created on another entry, never reparented.
        `nptc.db.roles.GRANT_DESIGNATION_UPDATE_SQL`'s column exclusion is the database invariant;
        this is the fail-loud Python layer.
        """
        if "entry_id" in self.__dict__ and self.__dict__["entry_id"] is not None:
            raise ImmutableFieldError(
                "Designation.entry_id is immutable and cannot be reassigned "
                f"from {self.__dict__['entry_id']!r} to {value!r}"
            )
        return value

    @validates("term")
    def _validate_term(self, _key: str, value: str) -> str:
        cleaned = clean_term(value)
        self.term_key = collision_key(cleaned)
        return cleaned

    @validates("language")
    def _validate_language(self, _key: str, value: str) -> str:
        return validate_language_tag(value)

    @property
    def length(self) -> int:
        """The character count of this designation's own `term`. Not the FR-85 published figure (see
        the module docstring). Never stored or settable.
        """
        return preferred_term_length(self.term)
