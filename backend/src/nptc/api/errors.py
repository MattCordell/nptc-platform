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

**Known gap, tracked rather than silent:** issue #47's remaining designation
constraints (malformed `use`, a duplicate active term, a second active
preferred designation in one language, the en-AU-preferred exclusion) are
enforced only at the database layer today (`IntegrityError`, unmapped
here) - a malformed `language`, an already-retired designation, and
(issue #49) a collision are the exceptions, given typed handlers below
(`DesignationLanguageError`, `DesignationAlreadyRetiredError`,
`DesignationCollisionError`) alongside `TermCleaningError`. There is no
HTTP surface for catalogue writes yet (#149/#150), so an unhandled
`IntegrityError` falls through to FastAPI's default 500 with no
caller-visible impact today - but #149/#150 must not simply reuse this
module unchanged: every remaining constraint needs either its own typed
exception raised before the flush (matching the precedent the typed
handlers below already set) or an explicit handler here, before those
routes ship, or a routine duplicate-synonym save 500s instead of
409/422ing.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nptc.api.dependencies import CredentialRequiredError, MalformedAuthorizationError
from nptc.auth.errors import TokenError
from nptc.auth.errors_authorisation import (
    AuthorisationError,
    ManualLinkRequiredError,
    MfaRequiredError,
)
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.designations import DesignationAlreadyRetiredError
from nptc.catalogue.errors import EntryNotFoundError, EntryVersionConflictError
from nptc.catalogue.search import EmptySearchQueryError, MalformedSearchCursorError
from nptc.catalogue.term_hygiene import DesignationLanguageError, TermCleaningError
from nptc.exports.semantic_tag import EmptyDisplayTermError, NotAServedFSNError
from nptc_shared.terminology import TerminologyConfigError

_logger = logging.getLogger(__name__)


class StoredFSNNotRenderableError(Exception):
    """A *read* path found a stored FSN it could not render (FR-83).

    Defined here rather than in `nptc.exports.semantic_tag` because it is
    an HTTP-status distinction, not a new rule about FSNs.
    `render_display_term` raises `NotAServedFSNError`/`EmptyDisplayTermError`,
    both `http_status = 422`, and 422 is the right answer on a write path:
    there, the caller supplied the FSN and the fault is in the request.

    On a read path it is the wrong answer, and wrong in a way that costs
    the platform the very outcome the loud failure exists for. `GET
    /catalogue/entries/{business_key}` is a well-formed request; the fault
    is entirely in stored data. A vendor's client reading 422 concludes its
    own request was malformed - it will not retry, and it will log the
    problem as its own bug - whereas FR-83's whole point is to get an
    administrator to look at the binding. 5xx is the class that says "this
    is our fault, escalate it", so a read path wraps both into this and
    `nptc.api.routers.catalogue` raises it.

    The 422 handlers for the two underlying errors are kept below, unused
    today: #149/#150's write surface is where they become reachable, and
    deleting them would leave that surface 500ing on a caller mistake.
    """

    http_status: ClassVar[int] = 500


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
_DETAIL_CHANGELOG_NOTE = (
    "A changelog note is required and must describe the change. It becomes the "
    'published History text, so single words like "update" or "fix" are not accepted.'
)
_DETAIL_TERM_CLEANING = (
    "This term could not be saved. It may be empty after whitespace cleaning, or "
    "contain a character that must be corrected by hand before it can be stored."
)
_DETAIL_DESIGNATION_LANGUAGE = "This language tag is not well-formed."
_DETAIL_ALREADY_RETIRED = "This designation has already been retired."
_DETAIL_SEARCH_QUERY_EMPTY = "Enter something to search for."
_DETAIL_SEARCH_CURSOR = (
    "This page cursor is not one this API issued. Pass a `next_cursor` value back "
    "unmodified, or start again from the first page."
)
#: Deliberately not "an internal error occurred": FR-83's refusal is a
#: *data* defect on one binding, and a caller who is told which kind of
#: defect it is can report something an administrator can act on. It names
#: no internal identifier, no served label and no stored value - the
#: business key the caller already sent is enough to identify the entry.
_DETAIL_DISPLAY_TERM = (
    "This entry has a code binding whose stored Fully Specified Name is not in the "
    "form the terminology server serves, so its display term cannot be rendered. "
    "The binding needs to be corrected by an administrator."
)
#: A configuration fault, so it names nothing a caller could act on and
#: nothing about the deployment (NFR-26): the variable and its bad value go
#: to the log, never to the response.
_DETAIL_SERVER_MISCONFIGURED = (
    "This service is not correctly configured and cannot serve this request. "
    "The problem has been logged for an administrator."
)
_DETAIL_DESIGNATION_COLLISION = (
    "This term matches another entry's preferred term or synonym, once case, spacing "
    "and punctuation are ignored. Choose a different term, or resolve the conflict on "
    "the other entry first."
)


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

    @app.exception_handler(EmptySearchQueryError)
    async def _handle_empty_search_query(
        _request: Request, exc: EmptySearchQueryError
    ) -> JSONResponse:
        # Not logged at all beyond DEBUG-worthiness: a blank search box is
        # the single most ordinary client mistake there is, and logging it
        # at INFO on a public, unauthenticated endpoint is an invitation to
        # fill the log with someone else's traffic.
        return JSONResponse(
            status_code=EmptySearchQueryError.http_status,
            content={"detail": _DETAIL_SEARCH_QUERY_EMPTY},
        )

    @app.exception_handler(MalformedSearchCursorError)
    async def _handle_malformed_search_cursor(
        _request: Request, exc: MalformedSearchCursorError
    ) -> JSONResponse:
        # The class only, never `str(exc)`: the message quotes the cursor,
        # which is caller-supplied text on a public endpoint (NFR-26/
        # NFR-35), exactly like a changelog note below.
        _logger.info("search cursor refused: %s", type(exc).__name__)
        return JSONResponse(
            status_code=MalformedSearchCursorError.http_status,
            content={"detail": _DETAIL_SEARCH_CURSOR},
        )

    @app.exception_handler(StoredFSNNotRenderableError)
    async def _handle_stored_fsn_not_renderable(
        _request: Request, exc: StoredFSNNotRenderableError
    ) -> JSONResponse:
        # The read-path counterpart of the two 422 handlers below, and a
        # 500 rather than a 422 for the reason this exception's own
        # docstring gives: the request was well-formed and the fault is in
        # stored data, so the status has to be the one that tells a vendor's
        # client "not your bug, escalate this".
        #
        # ERROR, not WARNING: FR-82 guarantees every stored `fsn` came from
        # the terminology server, so reaching here means that guarantee has
        # been broken for a *published* entry, and the endpoint is now
        # failing for every caller who asks for it until somebody looks.
        # Blanking the label and serving a 200 instead would hide a
        # corrupted binding indefinitely (FR-83).
        _logger.error("display term could not be rendered: %s", exc)
        return JSONResponse(
            status_code=StoredFSNNotRenderableError.http_status,
            content={"detail": _DETAIL_DISPLAY_TERM},
        )

    @app.exception_handler(TerminologyConfigError)
    async def _handle_terminology_config_error(
        _request: Request, exc: TerminologyConfigError
    ) -> JSONResponse:
        # A safety net, not the fix. `nptc.api.app.create_app` builds the
        # datatype registry eagerly precisely so a malformed `NPTC_TX_*`
        # value fails start-up rather than a request; this handler exists
        # for the paths that bypass the factory's warm-up (a test app, a
        # dependency override) so the failure is still a deliberate 500
        # with a logged cause rather than an unhandled traceback.
        _logger.error("terminology configuration refused: %s", exc)
        return JSONResponse(status_code=500, content={"detail": _DETAIL_SERVER_MISCONFIGURED})

    @app.exception_handler(NotAServedFSNError)
    async def _handle_not_a_served_fsn(_request: Request, exc: NotAServedFSNError) -> JSONResponse:
        # Reachable only from a *write* path (#149/#150), where the caller
        # supplied the FSN and 422 is correct. The read path wraps both of
        # these in `StoredFSNNotRenderableError` above; see its docstring.
        #
        # WARNING, not INFO - unlike every other refusal in this module,
        # this one is not a caller mistake at all: FR-82 guarantees every
        # stored `fsn` came from the terminology server, so reaching here
        # means that guarantee has been broken for a published entry and
        # somebody needs to look. Blanking the label instead and serving a
        # 200 would hide a corrupted binding indefinitely (FR-83).
        _logger.warning("display term could not be rendered: %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=NotAServedFSNError.http_status, content={"detail": _DETAIL_DISPLAY_TERM}
        )

    @app.exception_handler(EmptyDisplayTermError)
    async def _handle_empty_display_term(
        _request: Request, exc: EmptyDisplayTermError
    ) -> JSONResponse:
        # FR-83's second defensive assertion - same reasoning, same level,
        # same detail string as `NotAServedFSNError` above: from a caller's
        # point of view these are one fault ("this binding's FSN is not
        # renderable"), and splitting the message would tell them nothing
        # they could act on differently.
        _logger.warning("display term could not be rendered: %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=EmptyDisplayTermError.http_status, content={"detail": _DETAIL_DISPLAY_TERM}
        )

    @app.exception_handler(ChangelogNoteError)
    async def _handle_changelog_note_error(
        _request: Request, exc: ChangelogNoteError
    ) -> JSONResponse:
        # FR-37: a normal, expected refusal on a routine edit, not an
        # anomaly - INFO, not WARNING. NFR-26/NFR-35: a changelog note is
        # free text a user is exactly as likely to paste a name or a
        # ticket-with-PII into as any other free-text field, so - unlike
        # this module's other handlers, whose exception messages are safe
        # to log - only the exception *class* is logged here, never
        # `str(exc)`, which embeds the note itself.
        _logger.info("changelog note refused: %s", type(exc).__name__)
        return JSONResponse(status_code=exc.http_status, content={"detail": _DETAIL_CHANGELOG_NOTE})

    @app.exception_handler(TermCleaningError)
    async def _handle_term_cleaning_error(
        _request: Request, exc: TermCleaningError
    ) -> JSONResponse:
        # FR-63: applies to both CatalogueEntry.preferred_term and
        # Designation.term. The exception message quotes the term itself
        # (with any invisible character escaped, never raw, per NFR-38
        # test 2) - logged as the class only, not `str(exc)`, for the same
        # reason `_handle_changelog_note_error` above does: a term typed
        # directly into the platform is still user-supplied free text.
        _logger.info("term refused: %s", type(exc).__name__)
        return JSONResponse(status_code=exc.http_status, content={"detail": _DETAIL_TERM_CLEANING})

    @app.exception_handler(DesignationLanguageError)
    async def _handle_designation_language_error(
        _request: Request, exc: DesignationLanguageError
    ) -> JSONResponse:
        _logger.info("designation language tag refused: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_DESIGNATION_LANGUAGE}
        )

    @app.exception_handler(DesignationAlreadyRetiredError)
    async def _handle_designation_already_retired(
        _request: Request, exc: DesignationAlreadyRetiredError
    ) -> JSONResponse:
        _logger.info("retire refused, already retired: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_ALREADY_RETIRED}
        )

    @app.exception_handler(DesignationCollisionError)
    async def _handle_designation_collision_error(
        _request: Request, exc: DesignationCollisionError
    ) -> JSONResponse:
        # FR-05: a routine, expected refusal on a normal edit, not an
        # anomaly - INFO, not WARNING. Logged as the class and colliding
        # business_keys only, never `str(exc)` in full: the exception
        # message quotes the submitted term itself (NFR-26/NFR-35), which
        # is user-supplied free text exactly like a changelog note.
        _logger.info(
            "designation collision refused against %s",
            [c.business_key for c in exc.collisions],
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "detail": _DETAIL_DESIGNATION_COLLISION,
                "collisions": [
                    {
                        "severity": c.severity.value,
                        "business_key": c.business_key,
                        "preferred_term": c.preferred_term,
                    }
                    for c in exc.collisions
                ],
            },
        )
