"""The session endpoint the SPA calls after completing the PKCE exchange
(issue #41, NFR-01).

One route, deliberately. ADR-0021 puts the authorisation-code exchange in
the browser, so there is no callback endpoint here to receive a `code`:
by the time the SPA calls this, it already holds an access token. What it
does not know - and must never decide for itself (NFR-20) - is who that
token resolves to internally and what that user may do. That is this
endpoint's whole job.

It is also the first thing that makes the #43/#44 chain observable over
HTTP: a request here exercises verification, identity resolution and
permission derivation end to end.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import CurrentPrincipal
from nptc.auth.identity import UserRef

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.get("/me", summary="The current session's user, roles and permissions")
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
