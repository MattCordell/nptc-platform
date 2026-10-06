"""The session endpoint the SPA calls after completing the PKCE exchange (NFR-01), and the
terms-of-use endpoints beside it (NFR-45, NFR-47, ADR-0043).

`GET /auth/me` is one route about the session, deliberately. ADR-0021 puts the
authorisation-code exchange in the browser, so there is no callback endpoint here to receive a
`code`: by the time the SPA calls it, it already holds an access token. What the SPA does not
know, and must never decide for itself (NFR-20), is who that token resolves to internally and
what that user may do. That is its whole job.

The terms state is a sibling endpoint, not a field on `/auth/me`, so the session response keeps
one meaning. `GET /auth/terms` and `GET /auth/terms/{version}` serve the text the SPA renders,
so there is no second copy. `POST /auth/terms/acceptance` records an acceptance. It is the one
mutating route the terms gate exempts (`nptc.api.terms_gate.EXEMPT_ROUTES`), because a user who
has not accepted must be able to.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    ApiSettingsDep,
    AuditContextDep,
    CredentialRequiredError,
    CurrentPrincipal,
    get_session,
)
from nptc.api.errors import TermsVersionStaleResponse
from nptc.auth.identity import UserRef
from nptc.terms.acceptance import accept_terms, has_accepted
from nptc.terms.documents import TermsDocument, load_terms_document

router = APIRouter(prefix="/auth", tags=["auth"])


class ErrorResponse(BaseModel):
    """Every refusal this API makes has this shape - one sentence saying
    what to do next, and deliberately nothing else. It never names a role,
    a permission or an internal identifier (FR-44, NFR-04)."""

    detail: str


#: The refusals `nptc.api.errors` can produce on any authenticated route, declared
#: so `docs/api/openapi.json` carries the error contract and not just the happy
#: path.
AUTH_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: {
        "model": ErrorResponse,
        "description": (
            "No credential was presented where one is required, or the token "
            "could not be verified. Carries `WWW-Authenticate: Bearer`."
        ),
    },
    403: {
        "model": ErrorResponse,
        "description": (
            "Authenticated, but not permitted. When the permission would have "
            "been granted by a role suppressed for want of MFA, this carries an "
            'RFC 9470 `WWW-Authenticate: Bearer error="insufficient_user_'
            'authentication"` challenge instead of a bare denial (NFR-06).'
        ),
    },
    409: {
        "model": ErrorResponse,
        "description": (
            "The token resolved to more than one candidate account, or to an "
            "untrusted auto-link candidate. A human must resolve it (NFR-05)."
        ),
    },
}


class SessionResponse(BaseModel):
    """What the browser is allowed to know about its own session.

    `user` is `nptc.auth.identity.UserRef`, reused rather than redefined:
    it is this codebase's existing NFR-04 serialisation boundary and
    structurally excludes `app_user.id`. Defining a second response model
    with the same fields is exactly how that internal id eventually leaks.

    `permissions` is present because the shell needs to decide what to
    *render*, and rendering is presentation, not access control (NFR-20) -
    every one of these permissions is re-checked server-side at the
    endpoint that uses it. `roles` is included for the account screen
    (FR-40), which shows a user their own roles.
    """

    model_config = ConfigDict(frozen=True)

    authenticated: bool
    user: UserRef | None
    roles: list[str]
    permissions: list[str]
    #: NFR-06: lets the SPA offer a step-up prompt before the user walks
    #: into a 403, rather than only in reaction to one.
    mfa_satisfied: bool


@router.get(
    "/me",
    summary="The current session's user, roles and permissions",
    responses=AUTH_ERROR_RESPONSES,
)
def read_current_session(principal: CurrentPrincipal) -> SessionResponse:
    """Never 401s for an anonymous caller.

    "Who am I?" is a legitimate question with a legitimate answer for a
    signed-out visitor - `authenticated: false` - and the SPA asks it on
    every cold load, before it knows whether it has a session. Answering
    401 would make an ordinary first page load indistinguishable from a
    genuine credential failure. A *bad* token still 401s, because
    `current_principal` raises rather than degrading to anonymous.
    """
    return SessionResponse(
        authenticated=principal.user_id is not None,
        user=principal.user_ref,
        # Sorted so the response is stable across requests - an unsorted
        # frozenset would produce a different body each time, defeating
        # both HTTP caching and any test that compares whole bodies.
        roles=sorted(role.value for role in principal.roles),
        permissions=sorted(permission.value for permission in principal.permissions),
        mfa_satisfied=principal.mfa_satisfied,
    )


class TermsDocumentResponse(BaseModel):
    """One version of the terms. `text` is Markdown, served exactly as the version's file holds
    it, so the text a user accepted can be reproduced from the deployed service (NFR-47)."""

    model_config = ConfigDict(frozen=True)

    version: str
    effective_date: date
    text: str

    @classmethod
    def from_document(cls, document: TermsDocument) -> TermsDocumentResponse:
        return cls(
            version=document.version,
            effective_date=document.effective_date,
            text=document.text,
        )


class CurrentTermsResponse(TermsDocumentResponse):
    """The current terms, and whether the caller has accepted this version. `accepted` is
    `false` for an anonymous caller, who has accepted nothing."""

    accepted: bool


class TermsAcceptanceRequest(BaseModel):
    """`version` is the version the user was shown. The server refuses it when it is no longer
    the current one (ADR-0043)."""

    model_config = ConfigDict(frozen=True)

    version: str


class TermsAcceptanceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    accepted_at: datetime


_TERMS_NOT_FOUND_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **AUTH_ERROR_RESPONSES,
    404: {
        "model": ErrorResponse,
        "description": "No terms of use exist for the given version.",
    },
}

_TERMS_ACCEPTANCE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **AUTH_ERROR_RESPONSES,
    409: {
        "model": ErrorResponse | TermsVersionStaleResponse,
        "description": (
            "The named version is no longer the current one. The body carries "
            "`code: terms_version_stale` and the `current_version`, so the SPA can show the "
            "current text and ask again. The other 409 is the one every authenticated route "
            "shares: the token resolved to more than one candidate account (NFR-05)."
        ),
    },
}


@router.get(
    "/terms",
    summary="The current terms of use, and whether the caller has accepted them",
    responses=AUTH_ERROR_RESPONSES,
)
def read_current_terms(
    principal: CurrentPrincipal,
    session: Annotated[Session, Depends(get_session)],
    settings: ApiSettingsDep,
) -> CurrentTermsResponse:
    """Open to an anonymous caller, who is shown the text and told `accepted: false`."""
    document = load_terms_document(settings.terms_current_version)
    accepted = principal.user_id is not None and has_accepted(
        session, principal.user_id, current_version=document.version
    )
    return CurrentTermsResponse(
        **TermsDocumentResponse.from_document(document).model_dump(), accepted=accepted
    )


@router.get(
    "/terms/{version}",
    summary="One version of the terms of use, current or earlier",
    responses=_TERMS_NOT_FOUND_RESPONSES,
)
def read_terms_version(version: str) -> TermsDocumentResponse:
    """Serves any published version by its id, so the text behind an earlier acceptance can be
    reproduced (NFR-47). Needs no credential: the terms are public text."""
    return TermsDocumentResponse.from_document(load_terms_document(version))


@router.post(
    "/terms/acceptance",
    summary="Record that the caller accepted a version of the terms of use",
    responses=_TERMS_ACCEPTANCE_RESPONSES,
)
def accept_current_terms(
    body: TermsAcceptanceRequest,
    principal: CurrentPrincipal,
    session: Annotated[Session, Depends(get_session)],
    settings: ApiSettingsDep,
    audit: AuditContextDep,
) -> TermsAcceptanceResponse:
    """Requires a signed-in user and no permission: accepting the terms is the step that precedes
    holding any contribution right. Repeating an acceptance of the version the caller's latest
    record already names changes nothing."""
    if principal.user_id is None:
        raise CredentialRequiredError("accepting the terms requires a signed-in user")
    acceptance = accept_terms(
        session,
        audit,
        user_id=principal.user_id,
        version=body.version,
        current_version=settings.terms_current_version,
    )
    return TermsAcceptanceResponse(version=acceptance.version, accepted_at=acceptance.accepted_at)
