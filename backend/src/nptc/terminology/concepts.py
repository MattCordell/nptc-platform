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
is `bool | None` because a server may omit even a requested property (FHIR R4
does not oblige it to report one). `None` means "not reported", and calling that
active would mislead an editor (hazard H-05).

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
from nptc_shared.sctid import SCTID, InvalidSCTIDError, has_valid_format
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

__all__ = [
    "PROCEDURE_SCOPE_ECL",
    "ProcedureConcept",
    "ProcedureMatches",
    "ResolvedConcept",
    "classify_terminology_error",
    "resolve_concept",
    "search_procedures",
]

#: The code picker's scope: every descendant of `71388002 |Procedure|`, never the root itself.
#: Fixed here so a caller cannot widen it. The write path does not check FR-84, so this is the
#: only thing keeping an editor from binding a code outside Procedure.
PROCEDURE_SCOPE_ECL = "<71388002"

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


@dataclass(frozen=True, slots=True)
class ProcedureConcept:
    code: str
    au_preferred_term: str | None


@dataclass(frozen=True, slots=True)
class ProcedureMatches:
    """`total` is the server's own count of every match, or `None` when it reported none.
    A page that happens to be full is not evidence of more results, so no count is invented."""

    items: tuple[ProcedureConcept, ...]
    total: int | None


def search_procedures(
    client: TerminologyClient, q: str, *, count: int, edition: Edition = SNOMED_CT_AU
) -> ProcedureMatches:
    """One `ValueSet/$expand` of `PROCEDURE_SCOPE_ECL`, active concepts only, for the
    code picker (FR-26).

    `q` is a code or a term, told apart by shape. A code (6 to 18 digits) expands as
    `<71388002 AND <code>` with no `filter`, so a typed code outside Procedure comes back
    empty, where `$lookup` would accept it. A code that fails Verhoeff is an empty result
    with no request, not a 422, because an editor typing a code passes through invalid
    prefixes. Anything else is a `filter` on the display text.

    An empty match is a result, never an error. The server defines a term match: Ontoserver
    matches word prefixes, and the offline stub matches any substring.
    """
    text = q.strip()
    if has_valid_format(text):
        try:
            SCTID(text)
        except InvalidSCTIDError:
            return ProcedureMatches(items=(), total=0)
        ecl, term = f"{PROCEDURE_SCOPE_ECL} AND {text}", None
    else:
        ecl, term = PROCEDURE_SCOPE_ECL, text
    try:
        expansion = client.expand(
            ecl,
            edition=edition,
            count=count,
            active_only=True,
            filter=term,
            # FR-82: the picker shows the edition's own preferred term.
            display_language=edition.display_language,
        )
    except TerminologyConfigError:
        raise
    except TerminologyError as exc:
        raise classify_terminology_error(exc) from exc
    items = tuple(
        ProcedureConcept(code=concept.code, au_preferred_term=concept.display)
        for concept in expansion.concepts
    )
    return ProcedureMatches(items=items, total=expansion.total)


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
