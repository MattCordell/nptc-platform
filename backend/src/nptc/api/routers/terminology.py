"""FR-26's live concept lookup route (issue #240).

`GET /terminology/concepts/{code}` is the one HTTP surface over
`nptc.terminology.concepts.resolve_concept` - see that module's own
docstring for the field-derivation and error-classification rules this
router simply serialises. Follows `routers/catalogue_bindings.py`'s house
style: the return-type annotation drives the response model (never
`response_model=`), `ConfigDict(frozen=True)`, module-level `Final`
error-response dicts naming only the statuses this one route can actually
produce, and no try/except in the route body - every domain exception
carries `http_status` and is mapped centrally by `nptc.api.errors`.

**Not under `/catalogue`, and not tagged `catalogue-admin`.**
`test_api_public_response_hygiene.py::_catalogue_paths` scans every
`{API_PREFIX}/catalogue*` GET, skipping only `catalogue-admin`-tagged
ones, and hard-asserts every path parameter it finds is filled - a
`/catalogue/.../{code}` route would break that scanner outright, and
reusing the `catalogue-admin` tag to dodge it would silently enrol this
route in `test_api_catalogue_admin_read.py`'s separate "every
catalogue-admin GET 401s anonymously" assertion instead. This route
resolves nothing that belongs to the platform's own catalogue - it asks
the terminology server about a code nobody has bound to anything yet - so
it gets its own prefix and its own tag.

**Gated on `Permission.REGISTRY_READ`, not `CATALOGUE_BROWSE` or
`CATALOGUE_EDIT_PUBLISHED`.** ADR-0028 pre-authorises exactly this reuse:
`REGISTRY_READ` is "roles that can submit" (Provisional and up), and an
SCTID-resolution aid on a submission/edit form is precisely that audience.
`CATALOGUE_BROWSE` is held by `Role.ANON`, which would make this platform
an unauthenticated proxy amplifying traffic onto a shared public
Ontoserver with no availability commitment (OI-8) and no rate limiting yet
(#145) - ADR-0028 already rejected that over-correction once, for a less
abusable route. `CATALOGUE_EDIT_PUBLISHED` would work for #150 today but
is wrong on the requirement this route exists for: FR-26 names the
submitter, and FR-23 makes that Provisional and up, not Administrator.

**Edition is fixed to `SNOMED_CT_AU` in code, never a query parameter.**
`Edition.display_language` is set only on the AU edition on purpose
(`models.py`'s own docstring): sending it on both editions would leave a
caller unable to tell "the server does not recognise this language
reference set and silently fell back to some other preferred term" from
"this really is the AU preferred term" - exactly the ambiguity FR-82
exists to prevent on `au_preferred_term`. FR-47's dual-edition diff is a
P3 sweep concern, not this route's.

**No server-side cache, no bespoke rate limiter.** A cached FSN is the
stale-label hazard FR-82 exists to prevent, and `REGISTRY_READ` already
bounds and attributes traffic to signed-in, submission-capable callers -
the control #145's anonymous limiter cannot provide. `SCTID(code)` rejects
junk before any socket opens, and `OntoserverClient` already sits behind a
process-wide `lru_cache` with a keep-alive pool, so the marginal cost of a
call here is one round trip. Caching belongs client-side (TanStack Query's
`staleTime`), not here.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import get_terminology_client, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.auth.permissions import Permission
from nptc.terminology.concepts import resolve_concept
from nptc_shared.terminology import TerminologyClient

router = APIRouter(prefix="/terminology", tags=["terminology"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The caller is authenticated but does not hold `registry.read`.",
}
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server does not recognise this code in the AU edition - "
        "never a blank-but-successful resolution."
    ),
}
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The code is not a well-formed SNOMED CT identifier - a format or "
        "Verhoeff check-digit failure. No request reaches the terminology server "
        "for this case."
    ),
}
_RESPONSE_502: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server's response could not be used - an unparseable body, "
        'the wrong resource type, or a 4xx that was not itself an answer to "does '
        'this code exist". Names no URL, variable or upstream host.'
    ),
}
_RESPONSE_503: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server could not be reached, or a rate limit persisted "
        "through retries - the code field's live assist degrades; nothing else about "
        "the entry is affected (FR-54). May carry a `Retry-After` header."
    ),
}

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
    502: _RESPONSE_502,
    503: _RESPONSE_503,
}

TerminologyClientDep = Annotated[TerminologyClient, Depends(get_terminology_client)]
_READ = Depends(permission_dep(Permission.REGISTRY_READ))


class ConceptLookup(BaseModel):
    """One `$lookup`'s answer, on the wire.

    `code` is a string end-to-end (FR-06), echoed back rather than assumed
    - `$lookup` itself does not echo it, and a caller matching a late
    response to the field that asked needs it. `fsn` keeps its semantic
    tag intact and is nullable: the server may return no FSN designation
    at all, and `LookupResult.fully_specified_name` never falls back to
    `display`, which is a different thing. `active` is tri-state
    (`bool | None`) - `None` means the server did not report the
    `inactive` property, which is not the same as active (hazard H-05).
    `edition` is always `"au"` today - the honest source for a client's
    own `edition_hint`, so `"unknown"` stops being a value a form has to
    produce. `resolved_version` is FR-48: which release actually answered.

    Deliberately carries no `display_term` - see
    `nptc.terminology.concepts`'s own module docstring for why computing
    one here would risk a permanent 500 on a later read of whatever this
    value feeds.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    code: str
    fsn: str | None
    au_preferred_term: str | None
    active: bool | None
    edition: str
    resolved_version: str | None


@router.get(
    "/concepts/{code}",
    summary="Resolve one SNOMED CT code's served FSN, AU preferred term and active status",
    responses=_RESPONSES,
    dependencies=[_READ],
)
def get_concept(client: TerminologyClientDep, code: str) -> ConceptLookup:
    resolved = resolve_concept(client, code)
    return ConceptLookup(
        system=resolved.system,
        code=resolved.code,
        fsn=resolved.fsn,
        au_preferred_term=resolved.au_preferred_term,
        active=resolved.active,
        edition=resolved.edition,
        resolved_version=resolved.resolved_version,
    )
