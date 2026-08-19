"""The authorisation error hierarchy (issue #44, NFR-20) - deliberately a
module of its own, not added to `nptc.auth.errors`.

`nptc.auth.errors`'s own docstring states it is the *token-verification*
hierarchy and that every member is 401-shaped ("a future FastAPI
dependency can catch a single type and map it to a 401") - a future #41
router relies on `except TokenError -> 401`. Adding 403/409-shaped errors
there would falsify that contract. This module is the parallel hierarchy
for *authorisation* failures - the token verified fine; the question is
whether the resolved principal may do what they asked.

One base (`AuthorisationError`) so #41 gets a single `except`, with
`http_status` as a `ClassVar` rather than a hard-coded status in a shared
handler - a handler that assumed every subclass is 403 would silently
mis-map the two 409s below.
"""

from __future__ import annotations

from typing import ClassVar


class AuthorisationError(Exception):
    """Base for every reason a resolved principal is refused. Never raised
    directly - always one of the subclasses below, each carrying its own
    correct HTTP status."""

    http_status: ClassVar[int]


class PermissionDeniedError(AuthorisationError):
    """The principal is missing a required permission. `__str__` must
    name only the **permission** - never a role, never the internal
    user UUID (NFR-04/NFR-26) - see `nptc.auth.authorisation.
    require_permission` and `backend/tests/authz_support.py`'s assertion
    of exactly that."""

    http_status: ClassVar[int] = 403


class MfaRequiredError(PermissionDeniedError):
    """NFR-06: the permission would have been granted by a role the
    principal holds, but that role is suppressed because the request's
    token does not carry a satisfying `acr` claim (see
    `nptc.auth.principal.Principal.mfa_suppressed_roles`). A distinct
    subclass from a bare denial so a future #41 adapter can render an
    actionable RFC 9470 step-up challenge instead of a flat "you can't do
    that" for something the user genuinely can do once they re-authenticate."""

    http_status: ClassVar[int] = 403


class AccountClosedError(AuthorisationError):
    """The resolved `app_user` is closed. Practically unreachable - account
    closure deletes every linked `user_identity` row, so a closed user
    should never again resolve to `LinkOutcome.EXISTING` - but this is the
    fail-closed backstop `nptc.auth.principal.principal_for` raises if that
    invariant is ever violated."""

    http_status: ClassVar[int] = 403


class ManualLinkRequiredError(AuthorisationError):
    """`resolve_user_for_claims` returned `LinkOutcome.MANUAL_LINK_REQUIRED`
    (`user=None`): more than one candidate account matched, or the only
    candidate was found via an untrusted auto-link path. 409, not 401 -
    the token itself is valid and re-presenting it will never succeed on
    its own; a human must resolve the conflict before this principal can
    ever be derived."""

    http_status: ClassVar[int] = 409


class LastAdministratorError(AuthorisationError):
    """FR-01: the requested grant/revoke/closure/suspension would leave
    zero active Administrators. 409 - the request conflicts with the
    system's current state, not with the caller's permissions (the caller
    may well hold `role.grant.any`)."""

    http_status: ClassVar[int] = 409
