"""FR-26's live check: resolve one SNOMED CT code's served FSN, AU
preferred term and active status during form completion.

**One `$lookup`, never a designation scan.** `client.py`'s own docstring
states the rule this module leans on entirely: pass
`display_language=edition.display_language` and `LookupResult.display`
comes back as the edition's preferred term (FR-82); the FSN comes from
`LookupResult.fully_specified_name` regardless. A second rule - "the first
designation whose language is the AU tag" - would silently disagree with
that, because a `$lookup` designation's `use` does not distinguish
preferred from acceptable (the repo's own fixture
`lookup-active-concept.json` carries its AU-tagged designation as a plain
Synonym).

**Only `inactive` is requested**, not FR-46's inactivation-reason/
historical-association properties. They ride free on the same call and
would look like enough to build a "replace with the successor" affordance
- but FR-46 pairs a reason with an association through a table this module
has no business re-deriving a weaker copy of; that reading belongs to
FR-46/FR-47, not to a field-assist lookup. `active` is `bool | None`,
never a bare `bool`: FHIR R4 does not require a server to volunteer a
property nobody asked for, so `LookupResult.inactive` coming back `None`
means "not reported", not "active" - reporting that as `False` would tell
an editor a concept is active when the truth is "unknown" (hazard H-05).

**Classification order matters.** `TerminologyRateLimitError` and
`TerminologyTimeoutError` both subclass a broader type
(`TerminologyStatusError`, `TerminologyTransportError` respectively), so
`classify_terminology_error` below checks absence first, then the
rate-limit case specifically (it carries `retry_after`), then the general
retryable cases, and only falls through to the catch-all once nothing more
specific matched. **The catch-all is 502, deliberately never 404**: an
unclassified `TerminologyError` - most notably
`StubTerminologyClient`'s own `StubNotSeededError`, a bare
`TerminologyError` that is neither a status nor a transport failure - read
as "not found" would let an unseeded stub answer every lookup with a
clean-looking absence instead of the test-authoring defect it actually is,
reproducing inside the suite the exact hazard `stub.py`'s own module
docstring exists to prevent.

**`classify_terminology_error`'s `not_found` is a caller-supplied factory,
not a fixed exception type.** Lifted for issue #247's coded-property
values route to reuse without a second, independently-written ladder
(FR-74/ADR-0001's own reasoning, applied within the backend rather than
across the shared/backend boundary) - but that route's `$expand`-based
resolution has no single *code* to report absent, only a value set that
either resolves or is itself misconfigured, so it passes no `not_found`
callback at all and lets an absence-shaped failure fall through to the
`TerminologyUpstreamError` catch-all instead of manufacturing a
`ConceptNotFoundError` for a `code` that was never asked about.

**`TerminologyConfigError` never reaches `classify_terminology_error` at
all.** It is a `TerminologyError` subclass (a malformed `NPTC_TX_*` value),
and `classify_terminology_error` would otherwise fold it into the 502
catch-all - contradicting
`nptc.terminology.errors`'s own claim that a config fault is "already
mapped to 500" by `nptc.api.errors`. `resolve_concept` re-raises it
unchanged before the generic `except TerminologyError`, so that mapping
holds regardless of which `TerminologyClient` implementation raises it and
from where.
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

#: Only `inactive` - see the module docstring for why FR-46's other
#: properties are deliberately not requested here.
_LOOKUP_PROPERTIES: tuple[str, ...] = ("inactive",)


@dataclass(frozen=True, slots=True)
class ResolvedConcept:
    """One `$lookup`'s answer, shaped for `nptc.api.routers.terminology`'s
    `ConceptLookup` response model - see that model's own docstring for why
    each field is what it is."""

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
    """FR-26: one `CodeSystem/$lookup`, classified into `ResolvedConcept`
    or one of `nptc.terminology.errors`'s three HTTP-status-bearing
    exceptions.

    `SCTID(code)` runs first and reaches no socket at all on failure - a
    malformed or Verhoeff-failing code is `InvalidSCTIDError`, already
    mapped to 422 by `nptc.api.errors`, before `client.lookup` is ever
    called. `code` itself, not the validated `SCTID.value`, is what is
    looked up and echoed back: `SCTID` is a validation gate here, not a
    normalisation step, and there is nothing to normalise - FR-06's format
    is exactly what a caller already sent.
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
        # Already mapped to 500 by nptc.api.errors - see this module's own
        # docstring for why this must never reach _classify below.
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
        # The server's own answer, not a locally-held constant - see issue
        # #240 review: a hardcoded system here would silently disagree with
        # a server that ever answered something else, and it would be a
        # second copy of a URI `nptc_shared.terminology.SNOMED_SYSTEM`
        # already carries (FR-74).
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
    """The shared FR-52/FR-54 ladder: any `TerminologyError` classified into
    one of `nptc.terminology.errors`'s three HTTP-status-bearing exceptions
    (or, for an absence, whatever `not_found()` builds) - see the module
    docstring for the ordering rationale and why the catch-all is 502, never
    404. `not_found` is omitted by a caller with no single code to report
    absent (issue #247's coded-property values route); an absence-shaped
    failure then falls through to the catch-all instead."""
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
