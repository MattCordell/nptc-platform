"""The `designation` table: catalogue-side preferred/synonym designations
(issue #47, FR-04, FR-24, FR-37, FR-85). See PRD §6.3.

**This table is catalogue-side only - it never stores a SNOMED CT-served
label.** There are three preferred-term-shaped strings in this platform, and
they live in three different places with three different edit postures:

- The RCPA/catalogue preferred term - `catalogue_entry.preferred_term`
  (issue #46), user-maintained, and exists *before* any code binding is
  created.
- The SNOMED CT-AU preferred term - `code_binding.au_preferred_term`
  (issue #48), stored exactly as served (FR-82), never editable.
- The SNOMED CT Fully Specified Name - `code_binding.fsn` (issue #48), as
  served, semantic tag intact, never editable.

Copying a served label into a `designation` row would destroy FR-82's
as-served guarantee and make an unchangeable label editable through this
table's write path, so `designation` holds only catalogue-authored
synonyms and non-en-AU preferred-term variants. The catalogue's own en-AU
preferred term stays exactly where #46 put it -
`catalogue_entry.preferred_term` - never duplicated into a row here; see
`_NO_EN_AU_PREFERRED_CHECK_SQL` below for the constraint that makes that a
database invariant rather than a convention.

**`length` has no column, anywhere.** FR-85's `Length` is specifically the
*catalogue's* preferred term's character count (PRD §6.5), which lives on
`CatalogueEntry.preferred_term` - see that model's own `length` property
for the field FR-85 is actually about. `Designation.length` below is the
same computation applied to a designation's own `term` (a synonym or a
non-en-AU preferred variant) - useful for the same reason, but not itself
the FR-85 published figure. Neither gets a column: giving either one a
column at all, even one nothing ever writes to, would leave a seam a
future migration could accidentally populate. Both are bare Python
`@property`s, computed by `nptc.catalogue.term_hygiene.
preferred_term_length`, with deliberately no setter.

**Never `DELETE`d, only retired.** A designation that stops being current
moves to `status='retired'` (mirroring `CatalogueEntryStatus.WITHDRAWN`'s
own precedent) - `nptc.db.roles.REVOKE_DESIGNATION_DELETE_SQL` makes this a
privilege-level guarantee, the same trick already used for
`catalogue_entry`.

**`term_key` is FR-05's comparison form, stored and indexed (issue #49).**
The same `@validates("term")` hook that cleans the term also derives
`term_key` via `nptc_shared.similarity.collision_key` - casefolded, with
punctuation and whitespace folded to a separator, strictly stronger than
`clean_term`'s own whitespace-only fold. Stored rather than a bare
`@property` (unlike `length` above) because `nptc.catalogue.collisions`
needs an indexed equality lookup across every entry's designations, not a
per-row computation. Never independently meaningful once `term` is set,
so it is `__audit_ignored__`.
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


#: Plain string literals, never built from the `StrEnum`s above -
#: `test_sql_parameterisation.py`'s AST guard forbids SQL built from
#: runtime data, matching `catalogue_entry.py`'s own precedent.
_USE_CHECK_SQL = "use IN ('preferred','synonym')"
_STATUS_CHECK_SQL = "status IN ('active','retired')"
#: Same shape as `user_identity.py`'s `issuer_not_blank`/`subject_not_blank` -
#: a blank term that silently matches every other blank term is a defect
#: worth a constraint, not just a code-review note.
_TERM_NOT_BLANK_SQL = "length(btrim(term)) > 0"
#: A syntactic BCP-47 well-formedness check at the database layer too, not
#: only in `nptc_shared.language.is_well_formed_language_tag` (which this
#: model's own `@validates("language")` hook calls) - so a row inserted by
#: anything other than that hook (a future bulk-load path, say) still
#: can't carry a malformed tag. Built from `LANGUAGE_TAG_PATTERN.pattern`
#: rather than hand-copied, so the two can never silently diverge -
#: `test_designation_language_check_matches_the_shared_pattern` pins this.
_LANGUAGE_CHECK_SQL = f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'"
#: The database-layer half of "the catalogue en-AU preferred term lives in
#: exactly one place" (module docstring) - `catalogue_entry.preferred_term`,
#: never a `designation` row. A non-en-AU catalogue-authored preferred
#: variant is still permitted.
_NO_EN_AU_PREFERRED_CHECK_SQL = "NOT (use = 'preferred' AND language = 'en-AU')"


class Designation(Base):
    __tablename__ = "designation"

    # nptc.audit.policy (issue #37, NFR-08): every real column classified.
    # id/created_at/updated_at are ignored, matching every other model's
    # own treatment of its primary key and bookkeeping timestamps.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"entry_id", "term", "use", "language", "status"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "updated_at", "term_key"}
    )

    __table_args__ = (
        CheckConstraint(_USE_CHECK_SQL, name="use"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status"),
        CheckConstraint(_TERM_NOT_BLANK_SQL, name="term_not_blank"),
        CheckConstraint(_LANGUAGE_CHECK_SQL, name="language"),
        CheckConstraint(_NO_EN_AU_PREFERRED_CHECK_SQL, name="no_en_au_preferred"),
        # Explicit names throughout: NAMING_CONVENTION's "ix" rule keys off
        # `column_0_label` alone, so two partial indexes both leading with
        # `entry_id` would otherwise both autogenerate the same name and
        # collide.
        Index(
            "ix_designation_one_active_preferred_per_entry_language",
            "entry_id",
            "language",
            unique=True,
            postgresql_where=text("status = 'active' AND use = 'preferred'"),
        ),
        # No duplicate active (entry_id, term_key, language) - the same
        # synonym attached twice to one entry (whether from a doubled
        # delimiter, a whitespace variant, or now a case/punctuation
        # variant, PRD Appendix A.4) collapses to one row rather than being
        # representable at all. Keyed on `term_key`, not `term`, since
        # issue #49 - two surface forms that fold to the same collision key
        # are one synonym for this purpose, matching `add_synonyms`'s own
        # dedup-before-insert behaviour.
        Index(
            "ix_designation_no_duplicate_active_term",
            "entry_id",
            "term_key",
            "language",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # FR-05, issue #49: an indexed lookup for a cross-entry collision -
        # `nptc.catalogue.collisions` filters this by `status`/entry status
        # in the query itself, so a plain btree (not partial) index is
        # sufficient here.
        Index("ix_designation_term_key", "term_key"),
        # FR-14/FR-15, issue #142: the synonym half of the public catalogue
        # search - see `CatalogueEntry`'s own trigram index for why this is
        # declared in the model as well as in migration 0011. Partial on
        # `status = 'active'`, unlike the entry-side index: a retired
        # synonym is history, never a way into the catalogue, so search
        # never matches one.
        Index(
            "ix_designation_term_trgm",
            text("nptc_search_text(term)"),
            postgresql_using="gin",
            postgresql_ops={"nptc_search_text(term)": "gin_trgm_ops"},
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column named in __audit_fields__ above
    # (issue #37) - without it, diff_instance's load_history() call cannot
    # recover a prior value reassigned on this instance before it was ever
    # (re)loaded.
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogue_entry.id"),
        nullable=False,
        index=True,
        active_history=True,
    )
    term: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # FR-05/issue #49: derived from `term` by the same `@validates` hook
    # below - never independently assignable through the ORM. See the
    # module docstring. `server_default=''` exists only so a raw INSERT
    # that bypasses the ORM entirely (every `backend/tests/test_db_*.py`
    # constraint/privilege test) still satisfies `NOT NULL` - every write
    # that goes through `Designation` itself always supplies the real,
    # computed value, which overrides this default.
    term_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # Plain literals, not built from the `StrEnum`s above -
    # `test_sql_parameterisation.py`'s AST guard forbids a SQL call's first
    # argument being built from an f-string, matching `catalogue_entry.py`'s
    # own `status` column precedent.
    use: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'synonym'"), active_history=True
    )
    language: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'en-AU'"), active_history=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"), active_history=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @validates("entry_id")
    def _validate_entry_id_immutable(self, _key: str, value: uuid.UUID) -> uuid.UUID:
        """A designation is retired and re-created on a different entry,
        never reparented (matching `CatalogueEntry.business_key`'s own
        immutability guard) - `nptc.db.roles.GRANT_DESIGNATION_UPDATE_SQL`'s
        column exclusion is the actual database invariant; this is the
        fail-loud Python-level layer."""
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
        """The character count of this designation's own `term` - the
        same computation FR-85 requires for `CatalogueEntry.
        preferred_term` (see that model's own `length` property for the
        field FR-85 actually publishes), applied here for a synonym or a
        non-en-AU preferred variant. Never stored, never settable - see
        the module docstring for why this is a bare `@property` with no
        backing column at all."""
        return preferred_term_length(self.term)
