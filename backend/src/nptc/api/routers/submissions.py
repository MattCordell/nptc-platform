"""The submission create routes and the duplicate check (FR-23, FR-24, FR-25, FR-26, FR-27, FR-35,
FR-80, NFR-08, NFR-45).

The HTTP adapter over `nptc.submissions.new_test` and `nptc.submissions.amendment`; it re-implements
no domain rule. A domain
exception carries `http_status` and `nptc.api.errors` maps it, so the route body has no try/except.

**Authorisation:** `Permission.SUBMISSION_CREATE` (FR-44, FR-80). Provisional, Member, Reviewer and
Administrator hold it and Observer does not. The terms gate applies to every non-GET route, so the
route inherits it (NFR-45).

**An amendment has its own route and permission (FR-35).** `POST /submissions/amendments` needs
`Permission.AMENDMENT_PROPOSE`, which the same four roles hold. A separate route keeps the gate a
static `permission_dep`, which `test_authz_inventory.py` can check, where a `kind` field in one body
would need a gate chosen at run time. It names the entry by `entry_business_key`. An unknown key is
a 404, and an entry that is not `active` is a 409 that names the status. There is no duplicate check
and there are no property values, because an amendment would match its own entry. The reference link
is optional, and a link that is given is fetched like a new test's.

**A refused code is a 422, not a 404.** The route's resource exists; what is wrong is a value in
the body. A 404 here would tell a client the endpoint is missing. The terminology server being
unreachable stays a 503, because the caller's code may be perfectly good.

**The request has no `length`, no FSN and no unknown field** (`extra="forbid"`). Length is computed
from the term (FR-24, FR-85) and the FSN is whatever the terminology server returns for the code
(FR-82), so neither is the caller's to supply.

**The reference link is required and is fetched before anything is saved (FR-27).** The server
checks the link's status only, refuses every internal address, and stores the link with the time
and status it saw. A link that fails the check is a 422 with a reason the submitter can act on. A
deployment with no outbound internet access is a 503, because the link may be fine. The route is a
plain `def`, so FastAPI runs it in a worker thread and the checker's wait never stalls the event
loop.

**The duplicate check is a separate read (FR-25).** `POST /submissions/duplicate-check` takes the
terms and the code and returns the active catalogue entries and open submissions that match. It
writes nothing, so it emits no audit event, and it checks the code's format only, so it works while
the terminology server is down. `POST /submissions` runs the same check before it saves. A match
without `confirm_not_duplicate` is a 409 that carries the matches and saves nothing. A confirmed
submission stores the time and the matches the server found, never a list the caller sent. A
`POST` is used because the terms are free text that may be long, and a `GET` would put them in a
URL that proxies log (NFR-35).

**A quota refusal is a 429 (FR-43).** Both create routes first count the caller's earlier
submissions, new tests and amendments together, against the quota their roles allow
(`nptc.submissions.quota`). A refusal is returned and sent as a normal response, never raised,
because the request session rolls back on an exception and would take the audit event with it. An
hourly refusal carries `Retry-After`, and a lifetime one does not, because waiting does not lift it.
A caller whose earlier submission is still being processed is refused too, with `limit` of
`concurrent` and a short `Retry-After`, rather than made to wait for the per-user lock while holding
a database connection. The check runs after the permission gate and the body validation, and before any terminology or
reference call, so an over-limit caller costs the server no network request. The duplicate check
writes nothing and is not counted.

**No `submitter` in the response.** Who submitted a record is FR-42's rule, which the read routes
own; this route returns the submitter's own copy of the organisation and nothing that names a user.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    ApiSettingsDep,
    AuditContextDep,
    CurrentPrincipal,
    ReferenceCheckerDep,
    get_datatype_registry,
    get_session,
    get_terminology_client,
    permission_dep,
)
from nptc.api.errors import (
    DuplicateMatchItem,
    PropertyValidationResponse,
    SubmissionDuplicatesResponse,
    duplicate_match_item,
)
from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    SYNONYM_PROVENANCE,
    LabelProvenance,
    fsn_provenance,
)
from nptc.api.rate_limit import RateLimitedResponse
from nptc.api.routers.auth import ErrorResponse
from nptc.auth.authorisation import resolve_quota
from nptc.auth.permissions import Permission
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.property_values import PropertyValueInput
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.registry.handlers import DatatypeRegistry
from nptc.settings import ApiSettings
from nptc.submissions.amendment import AmendmentInput, create_amendment_submission
from nptc.submissions.duplicates import check_duplicates
from nptc.submissions.new_test import NewTestSubmissionInput, create_new_test_submission
from nptc.submissions.quota import QuotaLimit, QuotaRefusal, enforce_submission_quota
from nptc.submissions.reference_check import MAX_URL_LENGTH
from nptc_shared.terminology import TerminologyClient

router = APIRouter(prefix="/submissions", tags=["submissions"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `submission.create`, or has not accepted "
        "the current terms of use (the body then carries a `code` that says so)."
    ),
}
#: Three 422 body shapes reach a caller: a typed domain error (`ErrorResponse`: an unusable term,
#: a code the terminology server rules out, or a reference link that fails its check, each with its
#: own sentence), the field-level body
#: (`PropertyValidationResponse`), or a pydantic failure that never reaches the route body
#: (`HTTPValidationError`). The union lets the first two register in `components/schemas`.
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse | PropertyValidationResponse,
    "description": (
        "The request is not acceptable. `detail` says why for a term that cannot be cleaned and "
        "for a SNOMED CT code that is malformed, unknown to the AU edition, inactive, has no "
        "reported status or has no fully specified name, and for `notes` or `organisation` that "
        "carry an invisible character, and for a `reference_url` that is not a usable web address, "
        "points at an internal address, answers with a failing status, redirects too often, times "
        "out or cannot be found. `issues[]` names each property problem: an unknown, "
        "deprecated or out-of-scope property, a value its datatype refuses, or a property "
        "required for submission with no value. A missing `preferred_term`, an unrecognised "
        "field, or a part of the request over its size bound fails validation before the route "
        "runs."
    ),
    "content": {
        "application/json": {
            "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
        }
    },
}
_RESPONSE_500: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The service is misconfigured, not a caller mistake - a malformed `NPTC_TX_*` value. "
        "Retrying will not clear it."
    ),
}
_RESPONSE_502: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The terminology server's response could not be used.",
}
_RESPONSE_503: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server could not be reached, or a rate limit persisted through "
        "retries, or the platform has no outbound internet access to check the `reference_url`. "
        "Nothing was saved, and the same request can be sent again. May carry a `Retry-After` "
        "header."
    ),
}


class SubmissionQuotaResponse(BaseModel):
    """The 429 body when a caller has used up their submission quota (FR-43). `detail` is one
    sentence saying what happened and what to do next. `limit` says which limit was reached and
    `maximum` is its size, so a form can say more than the sentence does."""

    model_config = ConfigDict(frozen=True)

    detail: str
    limit: QuotaLimit
    maximum: int


_RESPONSE_429: Final[dict[str, Any]] = {
    "model": SubmissionQuotaResponse | RateLimitedResponse,
    "description": (
        "Either the caller has used up their submission quota (FR-43), or an anonymous or "
        "repeatedly rejected address has used up its request budget (FR-22, a `RateLimitedResponse`). "
        "For a quota refusal, new tests and amendments share one counter. `limit` is `hourly` when "
        "the quota is a number of submissions in a rolling hour, and the response then carries "
        "`Retry-After`, the whole seconds until a submission can be made. `limit` is `lifetime` "
        "when the quota is a total, and the response carries no `Retry-After`, because waiting "
        "does not lift it. Nothing was saved, and a used-up quota is recorded in the audit trail. "
        "`limit` is `concurrent` when an earlier submission from the same user is still being "
        "processed. The response then carries a short `Retry-After`, and the refusal is not "
        "audited, because no limit was reached."
    ),
    "headers": {
        "Retry-After": {
            "description": (
                "Whole seconds until the caller can try again. Always present on a request budget "
                "refusal, and on a quota refusal only when `limit` is `hourly` or `concurrent`."
            ),
            "schema": {"type": "integer", "minimum": 1},
        }
    },
}

_RESPONSE_409: Final[dict[str, Any]] = {
    "model": SubmissionDuplicatesResponse,
    "description": (
        "The submission matches an active catalogue entry or an open submission, and the request "
        "did not set `confirm_not_duplicate`. `matches[]` lists them. Nothing was saved. Send the "
        "request again with `confirm_not_duplicate` set to true if it is a different test."
    ),
}

_RESPONSE_403_AMENDMENT: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `amendment.propose`, or has not accepted "
        "the current terms of use (the body then carries a `code` that says so)."
    ),
}
_RESPONSE_404_AMENDMENT: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No catalogue entry has the `entry_business_key`.",
}
_RESPONSE_409_AMENDMENT: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The entry exists but is not `active` (it is a draft, deprecated or withdrawn), so it "
        "cannot be amended. `detail` names the status. Nothing was saved."
    ),
}
_RESPONSE_422_AMENDMENT: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The request is not acceptable. `detail` says why for an amendment with nothing left "
        "to propose (no synonym and no code, or only synonyms and a code the entry already "
        "has), for a synonym that cannot be cleaned, for a SNOMED CT code that "
        "is malformed, unknown to the AU edition, inactive, has no reported status or has no "
        "fully specified name, for `notes` or `organisation` that carry an invisible character, "
        "and for a `reference_url` that is not a usable web address, points at an internal "
        "address, answers with a failing status, redirects too often, times out or cannot be "
        "found. A malformed `entry_business_key`, an unrecognised field, or a part of the "
        "request over its size bound fails validation before the route runs."
    ),
}

_RESPONSES_CREATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    429: _RESPONSE_429,
    500: _RESPONSE_500,
    502: _RESPONSE_502,
    503: _RESPONSE_503,
}

_RESPONSES_AMENDMENT: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_AMENDMENT,
    404: _RESPONSE_404_AMENDMENT,
    409: _RESPONSE_409_AMENDMENT,
    422: _RESPONSE_422_AMENDMENT,
    429: _RESPONSE_429,
    500: _RESPONSE_500,
    502: _RESPONSE_502,
    503: _RESPONSE_503,
}

_RESPONSES_DUPLICATE_CHECK: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: {
        "description": (
            "The request is not acceptable: a term that cannot be cleaned, a SNOMED CT code that "
            "is malformed or fails its check digit, a missing `preferred_term`, an unrecognised "
            "field, or a part of the request over its size bound."
        ),
        "content": {
            "application/json": {
                "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
            }
        },
    },
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
TerminologyClientDep = Annotated[TerminologyClient, Depends(get_terminology_client)]
_CREATE = Depends(permission_dep(Permission.SUBMISSION_CREATE))
_PROPOSE_AMENDMENT = Depends(permission_dep(Permission.AMENDMENT_PROPOSE))


#: Bounds on one request. A property key costs a registry query and a coded value can cost a
#: terminology call, so a lowest-trust caller must not be able to make either unbounded. Each is far
#: above a real submission: a specimen property holds a handful of values, and a note of ten
#: thousand characters is several pages.
_MAX_TERM_LENGTH: Final = 500
_MAX_SYNONYMS: Final = 100
_MAX_NOTES_LENGTH: Final = 10_000
_MAX_ORGANISATION_LENGTH: Final = 500
_MAX_JUSTIFICATION_LENGTH: Final = 2_000
_MAX_VALUE_JSON_LENGTH: Final = 10_000
_MAX_PROPERTY_KEY_LENGTH: Final = 100
_MAX_PROPERTIES: Final = 25
_MAX_VALUES_PER_PROPERTY: Final = 25
_MAX_VALUES_IN_TOTAL: Final = 50
#: The longest SNOMED CT identifier (FR-06). Anything longer fails the format check, so it is
#: refused here, ahead of any lookup.
_MAX_SCTID_LENGTH: Final = 18

_Term = Annotated[str, StringConstraints(max_length=_MAX_TERM_LENGTH)]


class SubmissionPropertyValueRequest(BaseModel):
    """One value for a property, with the optional justification the registry's strength rule can
    ask for. Shaped like the catalogue's own property write, with a size bound on each part."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any
    justification: str | None = Field(default=None, max_length=_MAX_JUSTIFICATION_LENGTH)

    @field_validator("value")
    @classmethod
    def _value_is_not_huge(cls, value: Any) -> Any:
        if len(json.dumps(value, separators=(",", ":"))) > _MAX_VALUE_JSON_LENGTH:
            raise ValueError(
                f"a value must serialise to at most {_MAX_VALUE_JSON_LENGTH} characters"
            )
        return value


class CreateSubmissionRequest(BaseModel):
    """The body of `POST /submissions`.

    `reference_url` is the supporting link, an `http` or `https` address on port 80 or 443. The
    server fetches it before saving. `property_values` maps a property key to the complete value
    list for that property. A key with an empty list counts as absent. `organisation` left out means the profile's value, and a blank
    string means none. The size bounds are in the schema as `maxLength` and `maxItems`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preferred_term: _Term
    reference_url: str = Field(max_length=MAX_URL_LENGTH)
    synonyms: list[_Term] = Field(default_factory=list, max_length=_MAX_SYNONYMS)
    snomed_code: str | None = Field(default=None, max_length=_MAX_SCTID_LENGTH)
    property_values: dict[
        Annotated[str, StringConstraints(max_length=_MAX_PROPERTY_KEY_LENGTH)],
        Annotated[list[SubmissionPropertyValueRequest], Field(max_length=_MAX_VALUES_PER_PROPERTY)],
    ] = Field(default_factory=dict, max_length=_MAX_PROPERTIES)
    notes: str | None = Field(default=None, max_length=_MAX_NOTES_LENGTH)
    organisation: str | None = Field(default=None, max_length=_MAX_ORGANISATION_LENGTH)
    confirm_not_duplicate: bool = Field(
        default=False,
        description=(
            "Set to true to say this is a different test from any match. The server stores the "
            "matches it finds when it saves, which can include one a 409 did not list. It has "
            "no effect when nothing matches."
        ),
    )

    @model_validator(mode="after")
    def _values_in_total_are_bounded(self) -> CreateSubmissionRequest:
        total = sum(len(items) for items in self.property_values.values())
        if total > _MAX_VALUES_IN_TOTAL:
            raise ValueError(f"a submission holds at most {_MAX_VALUES_IN_TOTAL} property values")
        return self


class CreateAmendmentRequest(BaseModel):
    """The body of `POST /submissions/amendments`.

    `entry_business_key` names the active entry to amend. At least one new synonym or a
    `snomed_code` is needed. `reference_url` is optional, and when it is given the server fetches it
    before saving, as for a new test. `organisation` left out means the profile's value, and a
    blank string means none. The size bounds are in the schema as `maxLength` and `maxItems`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_business_key: str = Field(pattern=BUSINESS_KEY_PATTERN.pattern, max_length=40)
    synonyms: list[_Term] = Field(default_factory=list, max_length=_MAX_SYNONYMS)
    snomed_code: str | None = Field(default=None, max_length=_MAX_SCTID_LENGTH)
    reference_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    notes: str | None = Field(default=None, max_length=_MAX_NOTES_LENGTH)
    organisation: str | None = Field(default=None, max_length=_MAX_ORGANISATION_LENGTH)


class DuplicateCheckRequest(BaseModel):
    """The body of `POST /submissions/duplicate-check`: the terms and code a submission would
    carry, with the same size bounds as the create body. The code is checked for format and check
    digit only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preferred_term: _Term
    synonyms: list[_Term] = Field(default_factory=list, max_length=_MAX_SYNONYMS)
    snomed_code: str | None = Field(default=None, max_length=_MAX_SCTID_LENGTH)


class DuplicateCheckResponse(BaseModel):
    """Active catalogue entries first, then open submissions, each best first and capped. An empty
    list means nothing matched."""

    model_config = ConfigDict(frozen=True)

    matches: list[DuplicateMatchItem]


class SubmittedPropertyValue(BaseModel):
    """One stored value, as the registry's handler accepted it."""

    model_config = ConfigDict(frozen=True)

    value: Any
    justification: str | None


class SubmissionResponse(BaseModel):
    """The stored submission.

    `snomed_fsn` is the label the terminology server returned for `snomed_code`, never anything
    the caller sent (FR-82). `label_provenance` states which designation each label field is
    (FR-98): the suggested term is offered as the catalogue's preferred term, and the synonyms as
    synonyms. `entry_business_key` is the entry an amendment proposes to change, and null for a new
    test; an amendment's `preferred_term` is a copy of that entry's term when it was proposed. The
    three `reference_*` fields describe one check and are all present or all absent; a new test
    always has them. `duplicate_confirmed_at` is when the server saved a submission whose
    request carried `confirm_not_duplicate` and that matched something (FR-25), and is null
    otherwise.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    kind: str
    state: str
    entry_business_key: str | None
    preferred_term: str
    synonyms: list[str]
    snomed_code: str | None
    snomed_fsn: str | None
    property_values: dict[str, list[SubmittedPropertyValue]]
    notes: str | None
    reference_url: str | None
    reference_checked_at: datetime | None
    reference_status: int | None
    duplicate_confirmed_at: datetime | None
    organisation: str | None
    created_at: datetime
    row_version: int
    label_provenance: dict[str, LabelProvenance]


@router.post(
    "",
    summary="Submit a new test for the catalogue",
    status_code=201,
    response_model=SubmissionResponse,
    responses=_RESPONSES_CREATE,
    dependencies=[_CREATE],
)
def create_submission(
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
    registry: RegistryDep,
    terminology_client: TerminologyClientDep,
    reference_checker: ReferenceCheckerDep,
    settings: ApiSettingsDep,
    body: Annotated[CreateSubmissionRequest, Body()],
) -> SubmissionResponse | JSONResponse:
    refusal = enforce_submission_quota(
        session, ctx, quota=resolve_quota(principal), kind=SubmissionKind.NEW_TEST
    )
    if refusal is not None:
        return _quota_refusal_response(refusal)
    submission = create_new_test_submission(
        session,
        ctx,
        content=NewTestSubmissionInput(
            preferred_term=body.preferred_term,
            reference_url=body.reference_url,
            synonyms=body.synonyms,
            snomed_code=body.snomed_code,
            property_values={
                key: [
                    PropertyValueInput(value=item.value, justification=item.justification)
                    for item in items
                ]
                for key, items in body.property_values.items()
            },
            notes=body.notes,
            organisation=body.organisation,
            confirm_not_duplicate=body.confirm_not_duplicate,
        ),
        profile_organisation=principal.user_ref.organisation if principal.user_ref else None,
        registry=registry,
        terminology_client=terminology_client,
        reference_checker=reference_checker,
    )
    return _to_response(session, submission, settings)


@router.post(
    "/amendments",
    summary="Propose a change to a published entry",
    status_code=201,
    response_model=SubmissionResponse,
    responses=_RESPONSES_AMENDMENT,
    dependencies=[_PROPOSE_AMENDMENT],
)
def create_amendment(
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
    terminology_client: TerminologyClientDep,
    reference_checker: ReferenceCheckerDep,
    settings: ApiSettingsDep,
    body: Annotated[CreateAmendmentRequest, Body()],
) -> SubmissionResponse | JSONResponse:
    refusal = enforce_submission_quota(
        session, ctx, quota=resolve_quota(principal), kind=SubmissionKind.AMENDMENT
    )
    if refusal is not None:
        return _quota_refusal_response(refusal)
    submission = create_amendment_submission(
        session,
        ctx,
        content=AmendmentInput(
            entry_business_key=body.entry_business_key,
            synonyms=body.synonyms,
            snomed_code=body.snomed_code,
            reference_url=body.reference_url,
            notes=body.notes,
            organisation=body.organisation,
        ),
        profile_organisation=principal.user_ref.organisation if principal.user_ref else None,
        terminology_client=terminology_client,
        reference_checker=reference_checker,
    )
    return _to_response(session, submission, settings)


@router.post(
    "/duplicate-check",
    summary="Check a submission for duplicates before sending it",
    responses=_RESPONSES_DUPLICATE_CHECK,
    dependencies=[_CREATE],
)
def check_submission_duplicates(
    session: SessionDep,
    body: Annotated[DuplicateCheckRequest, Body()],
) -> DuplicateCheckResponse:
    matches = check_duplicates(
        session,
        preferred_term=body.preferred_term,
        synonyms=body.synonyms,
        snomed_code=body.snomed_code,
    )
    return DuplicateCheckResponse(matches=[duplicate_match_item(match) for match in matches])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _quota_detail(refusal: QuotaRefusal) -> str:
    if refusal.limit is QuotaLimit.CONCURRENT:
        return (
            "Another submission from your account is still being checked. "
            f"Try again in {_plural(refusal.retry_after_seconds or 1, 'second')}."
        )
    if refusal.limit is QuotaLimit.LIFETIME:
        return (
            f"Your account has reached its limit of {_plural(refusal.maximum, 'submission')} in "
            "total. Contact the catalogue team if you need to submit more."
        )
    if refusal.retry_after_seconds is None:
        return "Your account cannot make submissions at the moment. Contact the catalogue team."
    minutes = math.ceil(refusal.retry_after_seconds / 60)
    return (
        f"You have reached your limit of {_plural(refusal.maximum, 'submission')} in one hour. "
        f"Try again in {_plural(minutes, 'minute')}."
    )


def _quota_refusal_response(refusal: QuotaRefusal) -> JSONResponse:
    """A 429 returned rather than raised, so the request session commits the audit event."""
    headers = (
        {}
        if refusal.retry_after_seconds is None
        else {"Retry-After": str(refusal.retry_after_seconds)}
    )
    body = SubmissionQuotaResponse(
        detail=_quota_detail(refusal), limit=refusal.limit, maximum=refusal.maximum
    )
    return JSONResponse(status_code=429, content=body.model_dump(mode="json"), headers=headers)


def _entry_business_key(session: Session, submission: Submission) -> str | None:
    """The business key of the entry an amendment names, read from the stored link."""
    if submission.entry_id is None:
        return None
    return session.execute(
        select(CatalogueEntry.business_key).where(CatalogueEntry.id == submission.entry_id)
    ).scalar_one()


def _to_response(
    session: Session, submission: Submission, settings: ApiSettings
) -> SubmissionResponse:
    entry_business_key = _entry_business_key(session, submission)
    return SubmissionResponse(
        id=submission.id,
        kind=submission.kind,
        state=submission.state,
        entry_business_key=entry_business_key,
        preferred_term=submission.preferred_term,
        synonyms=list(submission.synonyms),
        snomed_code=submission.snomed_code,
        snomed_fsn=submission.snomed_fsn,
        property_values={
            key: [
                SubmittedPropertyValue(value=item["value"], justification=item["justification"])
                for item in items
            ]
            for key, items in submission.property_values.items()
        },
        notes=submission.notes,
        reference_url=submission.reference_url,
        reference_checked_at=submission.reference_checked_at,
        reference_status=submission.reference_status,
        duplicate_confirmed_at=submission.duplicate_confirmed_at,
        organisation=submission.organisation,
        created_at=submission.created_at,
        row_version=submission.row_version,
        label_provenance={
            "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
            "synonyms": SYNONYM_PROVENANCE,
            "snomed_fsn": fsn_provenance(settings),
        },
    )
