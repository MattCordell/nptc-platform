"""FR-26's live concept lookup route.

`GET /terminology/concepts/{code}` is the one HTTP surface over
`nptc.terminology.concepts.resolve_concept`, whose docstring holds the field-derivation and
error-classification rules this router serialises. A domain exception carries `http_status`
and `nptc.api.errors` maps it, so the route body has no try/except.
`docs/architecture/terminology-client.md` ("FR-26: the interactive lookup route") records the
reasoning behind the points below.

**Not under `/catalogue`, and not tagged `catalogue-admin`.**
`test_api_public_response_hygiene.py::_catalogue_paths` scans every `{API_PREFIX}/catalogue*`
GET except those tagged `catalogue-admin`, and asserts every path parameter is filled, so a
`/catalogue/.../{code}` route would break it. Reusing the `catalogue-admin` tag would instead
enrol the route in `test_api_catalogue_admin_read.py`'s "every catalogue-admin GET 401s
anonymously" assertion. The route resolves nothing in the platform's own catalogue, so it
has its own prefix and tag.

**Gated on `Permission.REGISTRY_READ`.** ADR-0028 anticipates this reuse: the audience is
submitters, Provisional and up (FR-23, FR-26). `CATALOGUE_BROWSE` is held by `Role.ANON` and
would make the platform an unauthenticated proxy onto a shared public Ontoserver (OI-8).
`CATALOGUE_EDIT_PUBLISHED` is Administrator-only.

**Edition is fixed to `SNOMED_CT_AU` in code, never a query parameter.**
`Edition.display_language` is set only on the AU edition (`nptc_shared.terminology.models`),
so a caller can tell the AU preferred term from a silent fallback to another one (FR-82).

**No server-side cache and no bespoke rate limiter.** A cached FSN is the stale-label hazard
FR-82 forbids, and `REGISTRY_READ` already limits traffic to signed-in, submission-capable
callers. `SCTID(code)` rejects junk before a socket opens, and `OntoserverClient` already
sits behind a process-wide `lru_cache` with a keep-alive pool. Caching belongs client-side.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import ApiSettingsDep, get_terminology_client, permission_dep
from nptc.api.labels import AU_PREFERRED_TERM_PROVENANCE, LabelProvenance, fsn_provenance
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
#: `TerminologyConfigError` (a malformed `NPTC_TX_*` value) reaching
#: `resolve_concept`. `create_app` builds the terminology client eagerly, so in
#: normal operation this is a start-up failure. The response documents the paths
#: that bypass that warm-up (a dependency override, a lazily configured client).
_RESPONSE_500: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The service is misconfigured, not a caller mistake - a malformed "
        "`NPTC_TX_*` value. Not produced by anything a well-formed request can "
        "trigger on its own; retrying will not clear it."
    ),
}

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
    500: _RESPONSE_500,
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

    `label_provenance` (FR-98, issue #144) covers both label fields even
    though `fsn` is nullable: a `None` value still has a designation and a
    semantic-tag state it *would* carry if the server returned one, so the
    descriptor is unconditional, never itself nullable.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    code: str
    fsn: str | None
    au_preferred_term: str | None
    active: bool | None
    edition: str
    resolved_version: str | None
    label_provenance: dict[str, LabelProvenance]


@router.get(
    "/concepts/{code}",
    summary="Resolve one SNOMED CT code's served FSN, AU preferred term and active status",
    responses=_RESPONSES,
    dependencies=[_READ],
)
def get_concept(client: TerminologyClientDep, settings: ApiSettingsDep, code: str) -> ConceptLookup:
    resolved = resolve_concept(client, code)
    return ConceptLookup(
        system=resolved.system,
        code=resolved.code,
        fsn=resolved.fsn,
        au_preferred_term=resolved.au_preferred_term,
        active=resolved.active,
        edition=resolved.edition,
        resolved_version=resolved.resolved_version,
        label_provenance={
            "fsn": fsn_provenance(settings),
            "au_preferred_term": AU_PREFERRED_TERM_PROVENANCE,
        },
    )
