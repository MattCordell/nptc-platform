"""Mapping the auth error families to HTTP responses (issue #41).

Two families, deliberately kept apart upstream and kept apart here:

- ``nptc.auth.errors.TokenError`` - every member is 401-shaped, as that
  module's docstring promises. "We could not establish who you are."
- ``nptc.auth.errors_authorisation.AuthorisationError`` - carries its own
  ``http_status`` ClassVar. "We know who you are; you may not do this."

The `AuthorisationError` handler reads ``exc.http_status`` rather than
matching on subclass. That is the whole reason the ClassVar exists: a
hand-written ladder is how ``ManualLinkRequiredError``/
``LastAdministratorError`` (both 409) eventually get flattened into 403
by someone adding a subclass and forgetting the ladder.

**Response bodies never name a role or an internal identifier** (FR-44,
NFR-04). `backend/tests/authz_app_support.py::assert_http_forbidden`
asserts exactly this, and these handlers are what must satisfy it: the
detail strings below are fixed, client-facing sentences, never
``str(exc)`` - the exception messages are diagnostic and do mention roles
and UUIDs, which is correct for a log and wrong for a response.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nptc.api.dependencies import CredentialRequiredError, MalformedAuthorizationError
from nptc.auth.errors import TokenError
from nptc.auth.errors_authorisation import (
    AuthorisationError,
    ManualLinkRequiredError,
    MfaRequiredError,
)
from nptc.catalogue.errors import EntryNotFoundError, EntryVersionConflictError

_logger = logging.getLogger(__name__)

#: RFC 9470 step-up challenge - pre-specified in
#: docs/architecture/permissions.md. `acr_values` names the LoA the realm's
#: `nptc loa-2 condition` maps to, which is also what
#: `AuthSettings.mfa_acr_values` defaults to.
_STEP_UP_CHALLENGE = 'Bearer error="insufficient_user_authentication", acr_values="2"'

#: Deliberately not `str(exc)`. See the module docstring.
_DETAIL_UNAUTHENTICATED = "Your credentials could not be verified. Sign in and try again."
_DETAIL_FORBIDDEN = "You do not have permission to do this."
_DETAIL_SIGN_IN_REQUIRED = "You need to sign in to do this."
_DETAIL_STEP_UP = (
    "This action requires multi-factor authentication. Sign in again and complete the second step."
)
_DETAIL_MANUAL_LINK = (
    "Your sign-in could not be matched to a single account. Contact an administrator "
    "to resolve this."
)
_DETAIL_CONFLICT = "This action conflicts with the current state of the system."
_DETAIL_VERSION_CONFLICT = (
    "This entry was changed by someone else since you loaded it. Review the "
    "conflicting changes and try again."
)
_DETAIL_NOT_FOUND = "No catalogue entry was found for the given identifier."


def _unauthenticated(detail: str) -> JSONResponse:
    # WWW-Authenticate on a 401 and never on a 403 - the pair endpoints
    # most reliably get backwards (assert_http_forbidden checks for it).
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TokenError)
    async def _handle_token_error(_request: Request, exc: TokenError) -> JSONResponse:
        # Logged at INFO, not WARNING: an expired token is the single most
        # common event on any authenticated API and is not an anomaly.
        # The message may name the issuer/audience but never the token.
        _logger.info("token refused: %s: %s", type(exc).__name__, exc)
        return _unauthenticated(_DETAIL_UNAUTHENTICATED)

    @app.exception_handler(MalformedAuthorizationError)
    async def _handle_malformed_authorization(
        _request: Request, exc: MalformedAuthorizationError
    ) -> JSONResponse:
        _logger.info("authorization header refused: %s", exc)
        return _unauthenticated(_DETAIL_UNAUTHENTICATED)

    @app.exception_handler(CredentialRequiredError)
    async def _handle_credential_required(
        _request: Request, exc: CredentialRequiredError
    ) -> JSONResponse:
        _logger.info("credential required: %s", exc)
        return _unauthenticated(_DETAIL_SIGN_IN_REQUIRED)

    @app.exception_handler(AuthorisationError)
    async def _handle_authorisation_error(
        _request: Request, exc: AuthorisationError
    ) -> JSONResponse:
        _logger.info("request refused: %s: %s", type(exc).__name__, exc)
        if isinstance(exc, MfaRequiredError):
            # 403 + a challenge, not 401: the credential was fine, the
            # authentication *strength* was not (RFC 9470).
            return JSONResponse(
                status_code=exc.http_status,
                content={"detail": _DETAIL_STEP_UP},
                headers={"WWW-Authenticate": _STEP_UP_CHALLENGE},
            )
        if isinstance(exc, ManualLinkRequiredError):
            return JSONResponse(
                status_code=exc.http_status, content={"detail": _DETAIL_MANUAL_LINK}
            )
        detail = _DETAIL_FORBIDDEN if exc.http_status == 403 else _DETAIL_CONFLICT
        return JSONResponse(status_code=exc.http_status, content={"detail": detail})

    @app.exception_handler(EntryVersionConflictError)
    async def _handle_entry_version_conflict(
        _request: Request, exc: EntryVersionConflictError
    ) -> JSONResponse:
        # Logged, not just returned: an FR-38 conflict is a normal editing
        # event, not an anomaly, but still worth a trace for support.
        _logger.info("stale row_version save refused: %s", exc)
        report = exc.report
        return JSONResponse(
            status_code=EntryVersionConflictError.http_status,
            content={
                "detail": _DETAIL_VERSION_CONFLICT,
                "business_key": report.business_key,
                "expected_row_version": report.expected_row_version,
                "current_row_version": report.current_row_version,
                "conflicts": [
                    {
                        "field": conflict.field,
                        "submitted": conflict.submitted,
                        "current": conflict.current,
                    }
                    for conflict in report.conflicts
                ],
                "changed_by": report.changed_by,
                "changed_at": report.changed_at.isoformat() if report.changed_at else None,
            },
        )

    @app.exception_handler(EntryNotFoundError)
    async def _handle_entry_not_found(_request: Request, exc: EntryNotFoundError) -> JSONResponse:
        # Logged at INFO: a stale bookmark or a race with a since-deleted
        # entry is ordinary, not an anomaly worth a louder level. The
        # exception message may name the business_key; the response body
        # never does, matching this module's own detail-string convention.
        _logger.info("entry not found: %s", exc)
        return JSONResponse(
            status_code=EntryNotFoundError.http_status, content={"detail": _DETAIL_NOT_FOUND}
        )
