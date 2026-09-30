"""FR-26's live check: resolve one SNOMED CT code's served FSN, AU
preferred term and active status during form completion.

**One `$lookup`, never a designation scan.** With
`display_language=edition.display_language`, `LookupResult.display` is the
edition's preferred term (FR-82) and the FSN is
`LookupResult.fully_specified_name` (see `nptc_shared.terminology.client`).
Scanning designations for the AU tag would disagree, because a `$lookup`
designation's `use` does not separate preferred from acceptable.

**Only `inactive` is requested.** FR-46's inactivation reason and historical
associations come on the same call, but FR-46/FR-47 own reading them. `active`
is `bool | None` because a server need not report a property nobody asked for.
`None` means "not reported", and calling that active would mislead an editor
(hazard H-05).

**Classification order matters.** `TerminologyRateLimitError` and
`TerminologyTimeoutError` subclass broader types, so `classify_terminology_error`
checks absence first, then the rate limit (it carries `retry_after`), then the
retryable cases. The catch-all comes last and is 502, never 404 (see
`nptc.terminology.errors.TerminologyUpstreamError`).

**`TerminologyConfigError` never reaches `classify_terminology_error`.** It is a
`TerminologyError` subclass, so the ladder would fold it into the 502 catch-all.
`nptc.api.errors` maps it to 500, so `resolve_concept` re-raises it first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nptc.terminology.errors import (
    ConceptNotFoundError,
    TerminologyUnavailableError,
    TerminologyUpstreamError,
)
from nptc_shared.sctid import SCTID
from nptc_shared.terminology import (
    SNOMED_CT_AU,
    Edition,
    TerminologyClient,
    TerminologyConfigError,
    TerminologyError,
    TerminologyRateLimitError,
    TerminologyTransportError,
    is_concept_absence,
)
from nptc_shared.terminology.errors import TerminologyStatusError

__all__ = ["ResolvedConcept", "classify_terminology_error", "resolve_concept"]

#: See the module docstring: only `inactive` is requested.
_LOOKUP_PROPERTIES: tuple[str, ...] = ("inactive",)


@dataclass(frozen=True, slots=True)
class ResolvedConcept:
    """One `$lookup` answer, shaped for `nptc.api.routers.terminology.ConceptLookup`."""

    system: str
    code: str
    fsn: str | None
    au_preferred_term: str | None
    active: bool | None
    edition: str
    resolved_version: str | None


def resolve_concept(
    client: TerminologyClient, code: str, *, edition: Edition = SNOMED_CT_AU
) -> ResolvedConcept:
    """FR-26: one `CodeSystem/$lookup`, classified into `ResolvedConcept` or one
    of `nptc.terminology.errors`'s HTTP-status-bearing exceptions.

    `SCTID(code)` runs first, so a malformed or Verhoeff-failing code raises
    `InvalidSCTIDError` (422 in `nptc.api.errors`) before any request. `code`
    itself, not `SCTID.value`, is looked up and echoed: `SCTID` is a validation
    gate here, not a normalisation step (FR-06).
    """
    SCTID(code)
    try:
        result = client.lookup(
            code,
            edition=edition,
            properties=_LOOKUP_PROPERTIES,
            display_language=edition.display_language,
        )
    except TerminologyConfigError:
        # See the module docstring: a config fault is a 500, not a 502.
        raise
    except TerminologyError as exc:
        raise classify_terminology_error(
            exc,
            not_found=lambda: ConceptNotFoundError(
                f"code {code!r} was not found by the terminology server"
            ),
        ) from exc

    inactive = result.inactive
    return ResolvedConcept(
        # The server's own answer: a constant here would be a second copy of
        # `nptc_shared.terminology.SNOMED_SYSTEM` (FR-74) that could disagree.
        system=result.system,
        code=code,
        fsn=result.fully_specified_name,
        au_preferred_term=result.display,
        active=None if inactive is None else not inactive,
        edition=edition.label,
        resolved_version=result.resolved_version,
    )


def classify_terminology_error(
    exc: TerminologyError, *, not_found: Callable[[], Exception] | None = None
) -> Exception:
    """The shared FR-52/FR-54 ladder: classify a `TerminologyError` into one of
    `nptc.terminology.errors`'s exceptions, or whatever `not_found()` builds for
    an absence. See the module docstring for the ordering.

    `not_found` is a caller-supplied factory so the coded-property values route
    (`nptc.catalogue.property_value_sources`) can reuse this ladder. That route
    resolves a value set through `$expand` and has no single code to report
    absent, so it passes none, and an absence-shaped failure reaches the
    `TerminologyUpstreamError` catch-all instead.
    """
    if not_found is not None and is_concept_absence(exc):
        return not_found()
    if isinstance(exc, TerminologyRateLimitError):
        return TerminologyUnavailableError(
            "terminology server rate limit persisted through retries", retry_after=exc.retry_after
        )
    if isinstance(exc, TerminologyTransportError):
        return TerminologyUnavailableError("terminology server could not be reached")
    if isinstance(exc, TerminologyStatusError) and exc.retryable:
        return TerminologyUnavailableError("terminology server error persisted through retries")
    return TerminologyUpstreamError(f"terminology server response could not be used: {exc}")
