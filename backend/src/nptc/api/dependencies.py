"""The FastAPI dependencies joining #43's verifier and #44's `Principal`
to an actual HTTP request (issue #41).

This module is the adapter ADR-0016 and ADR-0019 deferred. Everything it
calls already exists and is already tested as a library; nothing here
re-implements a check.

The chain, in order, is exactly:

    Authorization: Bearer <token>
      -> TokenVerifier.verify        (NFR-07: signature, iss, aud, exp)
      -> resolve_user_for_claims     (#42: internal app_user, NFR-04)
      -> principal_for               (#44: roles, permissions, MFA)

Nothing here decodes a token itself - `backend/tests/
test_token_verification_guard.py` is an AST check that would fail the
build if it did.
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.authentication import authenticate_identity
from nptc.auth.authorisation import require_permission
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission
from nptc.auth.principal import ANONYMOUS, Principal, principal_for
from nptc.auth.tokens import TokenVerifier
from nptc.db.session import session_scope
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.settings import ApiSettings, AuthSettings
from nptc_shared.terminology import OntoserverClient, TerminologyConfig

_BEARER_PREFIX = "bearer "


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    return AuthSettings()


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()


@lru_cache(maxsize=1)
def get_token_verifier() -> TokenVerifier:
    """Built once per process, not per request: constructing it may
    perform OIDC discovery (an outbound HTTP call), and the `SigningKeys`
    cache it wraps is only useful if it outlives a single request."""
    return TokenVerifier.from_settings(get_auth_settings())


@lru_cache(maxsize=1)
def get_datatype_registry() -> DatatypeRegistry:
    """The FR-77 datatype handler registry, built once per process.

    Built once for the same reason `get_token_verifier` is: the `code`
    handler wraps an `OntoserverClient`, which owns an HTTP connection pool
    that is only useful if it outlives a request. Construction itself opens
    no socket and performs no discovery, so this stays safe to build lazily
    on the first request that needs it.

    The registry is what keeps the property-serialisation path free of any
    datatype `switch` (ADR-0013, `backend/tests/test_datatype_dispatch.py`):
    a caller resolves a handler by datatype and calls it, and the only code
    that knows what the datatypes *are* is the handler package's own
    manifest.
    """
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=OntoserverClient(TerminologyConfig.from_env()),
                # FR-90/#56: no `LocalCodeLookup` implementation exists yet,
                # and `CodeHandler` raises `UnsupportedBindingError` rather
                # than silently passing a local-code binding it cannot check.
                # A loud refusal is the documented behaviour (ADR-0013 open
                # question 1), not a gap introduced here.
                local_code_lookup=None,
            )
        )
    )


def get_session() -> Iterator[Session]:
    yield from session_scope()


def bearer_token(request: Request) -> str | None:
    """The raw credential, or `None` when none was presented.

    `None` is deliberately not an error here: a public endpoint served to
    an anonymous visitor is a normal case (PRD Section 4.1). Turning
    "no credential" into a 401 is the *endpoint's* decision, made by
    requiring a permission `ANONYMOUS` lacks - not this function's.

    A present-but-unparseable header is a different thing from an absent
    one and is not silently downgraded to anonymous: `_MalformedAuthorizationError`
    surfaces as a 401, so a client sending `Authorization: Basic ...` or a
    bare token learns its credential was rejected rather than quietly
    receiving the public view.
    """
    header = request.headers.get("Authorization")
    if header is None:
        return None
    if not header.lower().startswith(_BEARER_PREFIX):
        raise MalformedAuthorizationError(
            "Authorization header is present but is not a Bearer credential"
        )
    token = header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise MalformedAuthorizationError("Authorization header carries an empty Bearer token")
    return token


class MalformedAuthorizationError(Exception):
    """A credential was presented but could not even be read as a Bearer
    token. Mapped to 401 by `nptc.api.errors`, alongside
    `nptc.auth.errors.TokenError`."""


class CredentialRequiredError(Exception):
    """A permission was required, the caller had no credential at all, and
    `ANONYMOUS` does not hold that permission.

    This exists to keep 401 and 403 apart - the pair
    `authz_support.assert_http_forbidden` singles out as the one endpoints
    most reliably get backwards. Without it, `require_permission` sees an
    anonymous `Principal`, finds the permission missing, and raises
    `PermissionDeniedError` -> 403 "you may not do this", when the honest
    answer is 401 "sign in and I will tell you". A 403 also gives a
    signed-out client no way to know that signing in would help.
    """


def _client_ip(request: Request) -> str | None:
    """The caller's IP, or `None` when it is absent or not an IP address.

    `request.client.host` is not guaranteed to be an IP: Starlette's own
    `TestClient` reports the literal `"testclient"`, and an ASGI server
    behind a unix socket reports the socket path. `AuditContext.actor_ip`
    feeds `nptc.audit.hashing.canonicalise_actor_ip`, which parses it as
    an `inet` and raises `ValueError` on anything else - so passing the
    raw value through would turn an unremarkable deployment topology into
    a 500 on every audited write.

    Recording `None` (an unknown address) is the honest answer here, and
    the one `AuditContext` already models; recording a placeholder string
    would be a fabricated fact in an append-only log.
    """
    if request.client is None:
        return None
    try:
        ipaddress.ip_address(request.client.host)
    except ValueError:
        return None
    return request.client.host


def _correlation_id(request: Request) -> uuid.UUID:
    """One id per request, minted on first use and reused thereafter.

    A fresh `uuid4()` per call would give the identity-resolution events
    (`user_identity.created` on a first login) a different correlation id
    from every later write in the *same* request - which defeats the one
    thing a correlation id is for. Stashed on `request.state` rather than
    threaded through, because the two call sites are separate FastAPI
    dependencies with no shared scope of their own.
    """
    existing: uuid.UUID | None = getattr(request.state, "correlation_id", None)
    if existing is not None:
        return existing
    correlation_id = uuid.uuid4()
    request.state.correlation_id = correlation_id
    return correlation_id


def request_audit_context(request: Request) -> AuditContext:
    """The per-request `AuditContext` for writes made *after* the actor is
    known. Note `actor_user_id=None`: see `bootstrap_audit_context`.

    `AuditContext.system()` is not usable on a request path - it asserts
    "no human actor", which is exactly the attribution NFR-08 exists to
    preserve.
    """
    return AuditContext(
        actor_user_id=None,
        actor_ip=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        correlation_id=_correlation_id(request),
    )


def bootstrap_audit_context(request: Request) -> AuditContext:
    """The context used *during* identity resolution.

    `actor_user_id` is unavoidably `None` here, and that is correct rather
    than a gap: `resolve_user_for_claims` emits `user_identity.created`
    (and, on first login, `user_role.granted`) for a user whose internal
    id does not exist until those very inserts run. There is no ordering
    in which a first login can attribute its own account-creation event to
    the account being created. `grant_role_unchecked(granted_by_user_id=
    None)` inside `_create_user` already encodes the same fact.

    The IP and user agent *are* carried, so the event is still attributable
    to a request even when it cannot be attributed to a prior user.
    """
    return request_audit_context(request)


def current_principal(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> Principal:
    """The resolved actor for this request - `ANONYMOUS` when no
    credential was presented.

    Every failure mode raises rather than degrading: an invalid token is a
    401 (`TokenError`), an ambiguous identity a 409
    (`ManualLinkRequiredError`), a closed account a 403
    (`AccountClosedError`). None of them returns `ANONYMOUS`, which would
    make "presented a bad token" indistinguishable from "presented none"
    in both the response and any log built from it.
    """
    token = bearer_token(request)
    if token is None:
        return ANONYMOUS

    audit = bootstrap_audit_context(request)
    identity = authenticate_identity(
        session,
        token,
        verifier=verifier,
        trusted_issuers=settings.trusted_issuers,
        audit=audit,
    )
    principal = principal_for(
        session,
        identity.resolution,
        claims=identity.claims,
        mfa_acr_values=settings.mfa_acr_values,
    )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def audit_context(
    request: Request,
    principal: CurrentPrincipal,
) -> AuditContext:
    """The `AuditContext` a state-changing route passes to the audit
    writer - identical to `request_audit_context` but attributed to the
    resolved actor (NFR-08)."""
    base = request_audit_context(request)
    return base.model_copy(update={"actor_user_id": principal.user_id})


def permission_dep(permission: Permission) -> Callable[[Principal], Principal]:
    """The adapter pre-specified in `docs/architecture/permissions.md`.

    It delegates to the real `require_permission` rather than re-checking
    anything, so FR-44's "authorise against a permission, never a role
    name" holds at every route by construction.
    """

    def dep(principal: CurrentPrincipal) -> Principal:
        try:
            return require_permission(permission)(principal)
        except PermissionDeniedError:
            # Only for a caller who presented no credential at all. An
            # authenticated user missing the permission still gets 403 -
            # and note `MfaRequiredError` is a PermissionDeniedError
            # subclass, but is never raised for ANONYMOUS (which holds no
            # suppressed roles), so it cannot be swallowed here.
            if principal.user_id is None:
                raise CredentialRequiredError(
                    f"{permission.value} requires an authenticated user"
                ) from None
            raise

    return dep
