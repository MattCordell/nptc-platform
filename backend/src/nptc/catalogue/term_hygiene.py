"""Pure term hygiene shared by `CatalogueEntry.preferred_term` and
`Designation.term` (FR-24, FR-63, FR-85, issue #47) - no SQLAlchemy, no
audit imports, nothing beyond `nptc_shared.text`/`nptc_shared.language`.

**Why this applies to both fields.** FR-85's computed length is defined
against the *catalogue's* preferred term (PRD §6.5: "it is simply the
character count of the RCPA preferred term"), which lives on
`CatalogueEntry.preferred_term`, not on any `Designation` row - ADR-0022
is explicit that the catalogue's own en-AU preferred term is never
duplicated into `designation` at all. The PRD §6.5 migration case (a
trailing non-breaking space shifting the published length) is therefore
a defect that must be cleaned on `preferred_term` itself, not merely on
synonym rows - FR-63's "normalisation on ingestion and prohibition at
entry" makes no field-shaped exception for the one field FR-85 actually
publishes.

**Why this is its own module rather than living in
`nptc.catalogue.designations`.** `nptc.db.models.catalogue_entry.
CatalogueEntry` and `nptc.db.models.designation.Designation` both need
`clean_term`/`preferred_term_length` for their own `@validates`/`length`
hooks. `nptc.catalogue.designations` (the audit-aware service layer)
imports `nptc.audit.recording`, which imports `nptc.audit.writer`, which
imports `nptc.db.models.audit`, which imports the `nptc.db.models`
package - and that package imports both model modules to register their
tables with `Base.metadata`. If a model imported `nptc.catalogue.
designations` directly, that chain would try to re-enter
`nptc.audit.writer` while it is still mid-import, which fails as
`ImportError: cannot import name 'AuditContext' from partially
initialized module`. Splitting the audit-free pieces out here breaks the
cycle: both models import only this module, which imports nothing from
`nptc.audit` or `nptc.db` at all.
"""

from __future__ import annotations

from typing import ClassVar

from nptc_shared.language import is_well_formed_language_tag
from nptc_shared.text import escape_invisible, find_invisible_characters, normalise_for_comparison


class TermCleaningError(ValueError):
    """Raised by `clean_term` when a term is empty after cleaning, or
    still carries an invisible character with no single deterministic
    repair (FR-63). Carries the same `http_status: ClassVar[int]`
    convention as `nptc.catalogue.errors` and
    `nptc.catalogue.changelog.ChangelogNoteError`."""

    http_status: ClassVar[int] = 422


class DesignationLanguageError(ValueError):
    """Raised by `Designation`'s `@validates("language")` hook when a
    language tag is not a well-formed BCP-47 tag - the Python-level
    counterpart to `designation.py`'s `_LANGUAGE_CHECK_SQL`, which is the
    actual database invariant. This is what makes `nptc_shared.language.
    is_well_formed_language_tag` load-bearing rather than dead code: it is
    the single implementation both the model's own validation and (via
    `test_designation_language_check_matches_the_shared_pattern`) the
    database `CHECK` constraint are proven to agree with."""

    http_status: ClassVar[int] = 422


def clean_term(term: str) -> str:
    """FR-63's "normalisation on ingestion and prohibition at entry",
    applied at write time rather than only at seed-import time.

    Collapses every normalisable space (a non-breaking space, a narrow
    no-break space - PRD Appendix A.1) to an ordinary space and strips the
    edges via the same `nptc_shared.text.normalise_for_comparison` the P0
    transform and FR-05 collision detection already share (ADR-0001) -
    this is FR-71's own doctrine that a normalisable space has exactly one
    correct repair, applied here for storage rather than comparison.

    Anything that survives that pass - a zero-width space, a bidi
    override, a genuine control character - has no single correct repair,
    so it is rejected rather than silently dropped. The message quotes the
    offending character via `escape_invisible`, never the raw character
    itself (NFR-38 test 2 prohibits an invisible character appearing
    verbatim in any generated output).
    """
    cleaned = normalise_for_comparison(term)
    if not cleaned:
        raise TermCleaningError("a term cannot be empty after whitespace cleaning (FR-63)")
    remaining = find_invisible_characters(cleaned)
    if remaining:
        raise TermCleaningError(
            f"{escape_invisible(cleaned)!r} contains an invisible character with "
            "no single deterministic repair (FR-63) and must be corrected before "
            "it can be saved"
        )
    return cleaned


def validate_language_tag(language: str) -> str:
    """Raises `DesignationLanguageError` unless `language` is a
    well-formed BCP-47 tag (`nptc_shared.language.
    is_well_formed_language_tag`); otherwise returns it unchanged."""
    if not is_well_formed_language_tag(language):
        raise DesignationLanguageError(f"{language!r} is not a well-formed BCP-47 language tag")
    return language


def preferred_term_length(term: str) -> int:
    """FR-85: the character count of `term` after the same whitespace
    cleaning applied at entry (`clean_term`) - computed here, never
    stored. This is the one function `CatalogueEntry.length`,
    `Designation.length`, and the future export/presentation layer
    (FR-85's "continues to be published, for continuity") must all call,
    so the published number can never drift from a second implementation.

    Deliberately takes the *stored* term, already cleaned by `clean_term`
    - PRD §6.5's migration note is exactly this: a term with a trailing
    non-breaking space publishes a *shorter* length once that space
    collapses to nothing after `.strip()`, for roughly one entry in five.
    """
    return len(normalise_for_comparison(term))
