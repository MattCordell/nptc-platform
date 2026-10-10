"""The submission create route (FR-23, FR-24, FR-26, FR-27, FR-80, NFR-08, NFR-45).

The HTTP adapter over `nptc.submissions.new_test`; it re-implements no domain rule. A domain
exception carries `http_status` and `nptc.api.errors` maps it, so the route body has no try/except.

**Authorisation:** `Permission.SUBMISSION_CREATE` (FR-44, FR-80). Provisional, Member, Reviewer and
Administrator hold it and Observer does not. The terms gate applies to every non-GET route, so the
route inherits it (NFR-45).

**A refused code is a 422, not a 404.** The route's resource exists; what is wrong is a value in
the body. A 404 here would tell a client the endpoint is missing. The terminology server being
unreachable stays a 503, because the caller's code may be perfectly good.

**The request has no `length`, no FSN and no unknown field** (`extra="forbid"`). Length is computed
from the term (FR-24, FR-85) and the FSN is whatever the terminology server returns for the code
(FR-82), so neither is the caller's to supply.

**No `submitter` in the response.** Who submitted a record is FR-42's rule, which the read routes
own; this route returns the submitter's own copy of the organisation and nothing that names a user.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    ApiSettingsDep,
    AuditContextDep,
    CurrentPrincipal,
    get_datatype_registry,
    get_session,
    get_terminology_client,
    permission_dep,
)
from nptc.api.errors import PropertyValidationResponse
from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    SYNONYM_PROVENANCE,
    LabelProvenance,
    fsn_provenance,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_properties import PropertyValueItemRequest
from nptc.auth.permissions import Permission
from nptc.catalogue.property_values import PropertyValueInput
from nptc.db.models.submission import Submission
from nptc.registry.handlers import DatatypeRegistry
from nptc.settings import ApiSettings
from nptc.submissions.new_test import NewTestSubmissionInput, create_new_test_submission
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
#: or a code the terminology server rules out, each with its own sentence), the field-level body
#: (`PropertyValidationResponse`), or a pydantic failure that never reaches the route body
#: (`HTTPValidationError`). The union lets the first two register in `components/schemas`.
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse | PropertyValidationResponse,
    "description": (
        "The request is not acceptable. `detail` says why for a term that cannot be cleaned and "
        "for a SNOMED CT code that is malformed, unknown to the AU edition, inactive, has no "
        "reported status or has no fully specified name. `issues[]` names each property problem: "
        "an unknown, deprecated or out-of-scope property, a value its datatype refuses, or a "
        "property required for submission with no value. A missing `preferred_term` or an "
        "unrecognised field fails validation before the route runs."
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
        "retries. Nothing was saved, and the same request can be sent again. May carry a "
        "`Retry-After` header."
    ),
}

_RESPONSES_CREATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422,
    500: _RESPONSE_500,
    502: _RESPONSE_502,
    503: _RESPONSE_503,
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
TerminologyClientDep = Annotated[TerminologyClient, Depends(get_terminology_client)]
_CREATE = Depends(permission_dep(Permission.SUBMISSION_CREATE))


class CreateSubmissionRequest(BaseModel):
    """The body of `POST /submissions`.

    `property_values` maps a property key to the complete value list for that property, each item
    shaped like the catalogue's own property write. A key with an empty list counts as absent.
    `organisation` left out means the profile's value, and a blank string means none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preferred_term: str
    synonyms: list[str] = Field(default_factory=list)
    snomed_code: str | None = None
    property_values: dict[str, list[PropertyValueItemRequest]] = Field(default_factory=dict)
    notes: str | None = None
    organisation: str | None = None


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
    synonyms.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    kind: str
    state: str
    preferred_term: str
    synonyms: list[str]
    snomed_code: str | None
    snomed_fsn: str | None
    property_values: dict[str, list[SubmittedPropertyValue]]
    notes: str | None
    organisation: str | None
    created_at: datetime
    row_version: int
    label_provenance: dict[str, LabelProvenance]


@router.post(
    "",
    summary="Submit a new test for the catalogue",
    status_code=201,
    responses=_RESPONSES_CREATE,
    dependencies=[_CREATE],
)
def create_submission(
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
    registry: RegistryDep,
    terminology_client: TerminologyClientDep,
    settings: ApiSettingsDep,
    body: Annotated[CreateSubmissionRequest, Body()],
) -> SubmissionResponse:
    submission = create_new_test_submission(
        session,
        ctx,
        content=NewTestSubmissionInput(
            preferred_term=body.preferred_term,
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
        ),
        profile_organisation=principal.user_ref.organisation if principal.user_ref else None,
        registry=registry,
        terminology_client=terminology_client,
    )
    return _to_response(submission, settings)


def _to_response(submission: Submission, settings: ApiSettings) -> SubmissionResponse:
    return SubmissionResponse(
        id=submission.id,
        kind=submission.kind,
        state=submission.state,
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
        organisation=submission.organisation,
        created_at=submission.created_at,
        row_version=submission.row_version,
        label_provenance={
            "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
            "synonyms": SYNONYM_PROVENANCE,
            "snomed_fsn": fsn_provenance(settings),
        },
    )
