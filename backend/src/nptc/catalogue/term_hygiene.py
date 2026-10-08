"""Pure term hygiene shared by `CatalogueEntry.preferred_term` and
`Designation.term` (FR-24, FR-63, FR-85; ADR-0022): no SQLAlchemy, no audit
imports, nothing beyond `nptc_shared.text`.

**Why this applies to both fields.** FR-85's computed length is defined
against the *catalogue's* preferred term (PRD SS6.5: "the character count of
the RCPA preferred term"), which lives on `CatalogueEntry.preferred_term`,
not on a `Designation` row. The PRD SS6.5 migration case, a trailing
non-breaking space shifting the published length, is therefore a defect to
clean on `preferred_term` itself. FR-63's "normalisation on ingestion and
prohibition at entry" makes no exception for the one field FR-85 publishes.

**Why this is its own module rather than part of
`nptc.catalogue.designations`.** The two models need `clean_term` and
`preferred_term_length` for their `@validates` and `length` hooks.
`designations`, the audit-aware service layer, imports `nptc.audit.recording`,
which imports `nptc.audit.writer`, which imports `nptc.db.models.audit`,
which imports the `nptc.db.models` package, and that package imports both
model modules to register their tables. A model importing `designations`
would re-enter `nptc.audit.writer` mid-import and fail with `ImportError:
cannot import name 'AuditContext' from partially initialized module`. This
module imports nothing from `nptc.audit` or `nptc.db`, which breaks the
cycle.
"""

from __future__ import annotations

from typing import ClassVar

from nptc_shared.text import escape_invisible, find_invisible_characters, normalise_for_comparison


class TermCleaningError(ValueError):
    """Raised by `clean_term` when a term is empty after cleaning, or still
    carries an invisible character with no single deterministic repair
    (FR-63)."""

    http_status: ClassVar[int] = 422


def clean_term(term: str) -> str:
    """FR-63's "normalisation on ingestion and prohibition at entry", applied
    at write time (ADR-0022).

    Collapses every normalisable space (a non-breaking space, a narrow
    no-break space; PRD Appendix A.1) to an ordinary space and strips the
    edges, via the `nptc_shared.text.normalise_for_comparison` that the P0
    transform and FR-05 collision detection share (ADR-0001). A normalisable
    space has exactly one correct repair (FR-71).

    Anything that survives that pass, such as a zero-width space, a bidi
    override or a control character, has no single correct repair, so it is
    rejected rather than silently dropped. The message quotes the character
    through `escape_invisible`, never raw: NFR-38 test 2 prohibits an
    invisible character appearing verbatim in generated output.
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


def preferred_term_length(term: str) -> int:
    """FR-85: the character count of `term` after the whitespace cleaning
    applied at entry (`clean_term`), computed here and never stored.
    `CatalogueEntry.length`, `Designation.length` and the future export layer
    (FR-85's "continues to be published, for continuity") must all call this,
    so the published number cannot drift from a second implementation.

    Takes the *stored* term, already cleaned by `clean_term`. PRD SS6.5's
    migration note is this case: a term with a trailing non-breaking space
    publishes a *shorter* length once the space collapses, for roughly one
    entry in five.
    """
    return len(normalise_for_comparison(term))


def exceeds_maximum_length(length: int, maximum: int) -> bool:
    """FR-86: a length equal to `maximum` is within it."""
    return length > maximum
