"""The authorisation error hierarchy (NFR-20), separate from `nptc.auth.errors`.

Every `nptc.auth.errors.TokenError` is 401-shaped and the API layer maps
`except TokenError` to a 401, so a 403 or 409 error cannot live there. These
errors mean the token verified and the question is whether the resolved
principal may do what they asked.

`http_status` is a `ClassVar` per subclass, because a handler that assumed every
subclass is 403 would mis-map the two 409s (ADR-0019).
"""

from __future__ import annotations

from typing import ClassVar


class AuthorisationError(Exception):
    """Base for every reason a resolved principal is refused. Raise a
    subclass, which carries its own HTTP status."""

    http_status: ClassVar[int]


class PermissionDeniedError(AuthorisationError):
    """The principal is missing a required permission. `__str__` must
    name only the **permission** - never a role, never the internal
    user UUID (NFR-04/NFR-26); `backend/tests/authz_support.py` asserts
    exactly that."""

    http_status: ClassVar[int] = 403


class MfaRequiredError(PermissionDeniedError):
    """NFR-06: a role the principal holds would grant the permission, but
    it is suppressed because the token lacks a satisfying `acr` claim (see
    `nptc.auth.principal.Principal.mfa_suppressed_roles`). A distinct
    subclass so the API layer can render an RFC 9470 step-up challenge
    instead of a flat denial."""

    http_status: ClassVar[int] = 403


class AccountClosedError(AuthorisationError):
    """The resolved `app_user` is closed. Practically unreachable, because
    closure deletes every linked `user_identity` row; this is the
    fail-closed backstop `nptc.auth.principal.principal_for` raises if that
    invariant is violated."""

    http_status: ClassVar[int] = 403


class ManualLinkRequiredError(AuthorisationError):
    """`resolve_user_for_claims` returned `LinkOutcome.MANUAL_LINK_REQUIRED`
    (`user=None`): more than one candidate account matched, or the only
    candidate was found via an untrusted auto-link path. 409, not 401: the
    token is valid and re-presenting it will not help, so a human must
    resolve the conflict."""

    http_status: ClassVar[int] = 409


class LastAdministratorError(AuthorisationError):
    """FR-01: the grant, revoke, closure or suspension would leave zero
    active Administrators. 409, because the request conflicts with the
    system's state, not with the caller's permissions (the caller may hold
    `role.grant.any`)."""

    http_status: ClassVar[int] = 409
