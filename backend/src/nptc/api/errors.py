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

**Issue #224 closed the designation gap this paragraph used to describe.**
Issue #47's remaining designation constraints - a duplicate active term,
a second active preferred designation in one language - now have typed
exceptions (`DuplicateActiveTermError`, `PreferredDesignationAlreadyActiveError`,
raised by `nptc.catalogue.designations.add_designation`/`amend_designation`
before the flush translates the `IntegrityError`, matching
`nptc.catalogue.bindings.create_binding`'s own precedent) and handlers
below. A malformed `use` and the en-AU-preferred exclusion
(`ck_designation_no_en_au_preferred`) are `CHECK` constraints, not unique
violations, so a constraint name alone cannot disambiguate what a caller
should fix - both are refused as a pydantic 422 at the request-body layer
instead (`nptc.api.routers.catalogue_designations`), before the ORM is
ever touched, the same way `catalogue_bindings.BindCodeRequest`'s
`_reject_blank` pre-empts `ck_code_binding_fsn_not_blank`. `acknowledge_
collision`'s own race is likewise now a typed
`DesignationCollisionAcknowledgementConflictError` (409) rather than an
unmapped `IntegrityError`. Issue #52's
`PropertyValidationError`/`PropertyDefinitionNotFoundError` handlers below
are the same situation in reverse: the write path (`nptc.catalogue.
property_values.save_property_values`) and its typed errors exist and are
handled here already, ahead of the HTTP route that will call it (a
follow-up issue, consumed by #151) - so that route inherits a working
422/404 from day one rather than repeating this module's own cautionary
tale.

**Issue #219's code-binding handlers follow the same rule.** Every
`CodeBinding*` exception in `nptc.catalogue.bindings` carries its own
`http_status` and is mapped below exactly like the designation/property
exceptions above - none of them is a raw `IntegrityError` fallthrough, and
none of them was reachable before #219 gave `nptc.api.routers.
catalogue_bindings` a route to raise them from.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import CredentialRequiredError, MalformedAuthorizationError
from nptc.auth.errors import TokenError
from nptc.auth.errors_authorisation import (
    AuthorisationError,
    ManualLinkRequiredError,
    MfaRequiredError,
)
from nptc.catalogue.bindings import (
    CodeBindingAlreadyActiveError,
    CodeBindingAlreadyRetiredError,
    CodeBindingCodeAlreadyBoundError,
    CodeBindingNotFoundError,
    CodeBindingNotRetiredError,
    CodeBindingSelfSupersessionError,
    CodeBindingWriteNotFoundError,
    InvalidCodeBindingEditionHintError,
    InvalidCodeBindingSystemError,
)
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.collisions import (
    DesignationCollisionAcknowledgementConflictError,
    DesignationCollisionError,
)
from nptc.catalogue.designations import (
    DesignationAlreadyRetiredError,
    DesignationNotFoundError,
    DuplicateActiveTermError,
    PreferredDesignationAlreadyActiveError,
)
from nptc.catalogue.errors import EntryNotFoundError, EntryVersionConflictError
from nptc.catalogue.property_value_sources import (
    PropertyNotCodeTypeError,
    PropertyValueSourceMisconfiguredError,
)
from nptc.catalogue.property_values import PropertyDefinitionNotFoundError, PropertyValidationError
from nptc.catalogue.search import EmptySearchQueryError, MalformedSearchCursorError
from nptc.catalogue.term_hygiene import DesignationLanguageError, TermCleaningError
from nptc.exports.semantic_tag import EmptyDisplayTermError, NotAServedFSNError
from nptc.registry.definitions import (
    DeprecatedPropertyWriteError,
    PropertyAlreadyDeprecatedError,
    PropertyConstraintsInvalidError,
    PropertyDatatypeUnknownError,
    PropertyDefinitionDeleteRefusedError,
    PropertyDefinitionKeyExistsError,
    PropertyKeyImmutableError,
    PropertyReactivationRefusedError,
    SystemPropertyDeprecationRefusedError,
)
from nptc.terminology.errors import (
    ConceptNotFoundError,
    TerminologyUnavailableError,
    TerminologyUpstreamError,
)
from nptc_shared.sctid import InvalidSCTIDError
from nptc_shared.terminology import TerminologyConfigError

_logger = logging.getLogger(__name__)


# --- the two 409 bodies that carry more than `detail` ----------------------
#
# Most refusals this module makes are an `ErrorResponse`: one sentence, and
# deliberately nothing else. Two are not, because a bare sentence would
# withhold exactly what the requirement exists to give the caller - FR-38's
# conflicting values, FR-05's colliding entry.
#
# Declared as models, and *constructed* by the handlers below rather than
# merely documented alongside them (issue #227 review): a router naming one
# of these in its `responses=` puts the real shape in
# `docs/api/openapi.json`, so #147's generated client can read the payload
# instead of typing the branch as `{detail}` and dropping it. Building the
# response through the model is what stops the declared schema and the
# emitted body from drifting - the failure mode a hand-written `content`
# block next to a hand-built `dict` invites.


class FieldConflictItem(BaseModel):
    """One field whose stored value moved under a caller between their read
    and their write. `submitted`/`current` are whatever that field holds -
    a term, a status, a flag - so they are deliberately untyped here."""

    model_config = ConfigDict(frozen=True)

    field: str
    submitted: Any
    current: Any


class VersionConflictResponse(BaseModel):
    """FR-38's 409 body: a stale `expected_row_version` on an entry-level
    write.

    FR-38's rationale rejects silent last-write-wins "because it produces an
    audit trail that records a change that was immediately and invisibly
    discarded", so the refusal has to let the caller *reconcile* rather than
    retry blind. `conflicts` is empty where the caller's submitted values do
    not themselves overlap what moved - still a refusal, because the version
    is the contract regardless - which is why `current_row_version` and
    `changed_by`/`changed_at` are populated even then.

    `changed_by` is a display name, never the actor's internal id
    (NFR-04/NFR-26), and is `null` for a system-initiated change or an
    account since pseudonymised on closure (NFR-17)."""

    model_config = ConfigDict(frozen=True)

    detail: str
    business_key: str
    expected_row_version: int
    current_row_version: int
    conflicts: list[FieldConflictItem]
    changed_by: str | None
    changed_at: datetime | None


class CollisionItem(BaseModel):
    """One FR-05 collision: the live entry a submitted term collides with,
    named by its public identifier and preferred term - never its internal
    id (NFR-04/NFR-26)."""

    model_config = ConfigDict(frozen=True)

    severity: str
    business_key: str
    preferred_term: str


class DesignationCollisionResponse(BaseModel):
    """FR-05's 409 body. PRD SS17.2 item 5 is explicit that the refusal names
    the colliding entry rather than returning a bare status, so an editor
    can go and look at it."""

    model_config = ConfigDict(frozen=True)

    detail: str
    collisions: list[CollisionItem]


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


class PreferredTermVersionRequiredError(Exception):
    """`POST .../designations/amendment` was asked to amend the catalogue's
    own en-AU preferred term, but carried no `expected_row_version`
    (issue #227, FR-38).

    Defined here rather than in `nptc.catalogue`, and raised rather than
    validated: the service layer has no such state - `save_entry` simply
    takes `expected_row_version` as a required argument, and there is no
    call it could reject. This is purely a fact about one HTTP request
    body, on the one route where the field is conditionally required.

    It cannot be a pydantic `model_validator` on the request either, which
    is where every other cross-field refusal on that route lives: whether
    the submitted term is the entry's own preferred term or a `designation`
    row is a database question (ADR-0022 splits the two storage homes), and
    a validator runs before the route body has a session. 422 all the same,
    so a caller sees the same status a missing required field would have
    produced had the requirement been expressible in the schema.
    """

    http_status: ClassVar[int] = 422


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
_DETAIL_PREFERRED_TERM_VERSION_REQUIRED = (
    "This term is the entry's own preferred term, so changing it needs the entry "
    "version you loaded. Reload the entry and send its `expected_row_version` with "
    "the amendment."
)
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
_DETAIL_DESIGNATION_NOT_FOUND = "No active designation was found for the given term."
_DETAIL_DUPLICATE_ACTIVE_TERM = (
    "This entry already has an active designation for this term, once case, spacing "
    "and punctuation are ignored."
)
_DETAIL_PREFERRED_DESIGNATION_ALREADY_ACTIVE = (
    "This entry already has an active preferred term in this language."
)
_DETAIL_COLLISION_ACKNOWLEDGEMENT_CONFLICT = (
    "This collision was just acknowledged by another request. No further action is needed."
)
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
_DETAIL_PROPERTY_VALIDATION = (
    "One or more of the values you entered could not be saved. Review the listed "
    "fields and correct them before saving again."
)
_DETAIL_PROPERTY_DEFINITION_NOT_FOUND = "No property definition was found for the given key."
_DETAIL_PROPERTY_NOT_CODE_TYPE = (
    "This property does not have a coded datatype, so it has no bound value source to list."
)
_DETAIL_INVALID_SCTID = (
    "This is not a valid SNOMED CT identifier. It must be 6 to 18 digits and pass the "
    "check-digit calculation."
)
_DETAIL_BINDING_ALREADY_RETIRED = "This code binding has already been retired."
_DETAIL_BINDING_ALREADY_ACTIVE = "This entry already has an active code binding."
_DETAIL_BINDING_CODE_ALREADY_BOUND = (
    "This code is already actively bound to another catalogue entry."
)
_DETAIL_BINDING_NOT_RETIRED = "This code binding must be retired before it can be replaced."
_DETAIL_BINDING_SELF_SUPERSESSION = "A code binding cannot replace itself."
_DETAIL_INVALID_EDITION_HINT = "This is not a recognised edition hint."
_DETAIL_INVALID_SYSTEM = "The code system cannot be blank."
_DETAIL_BINDING_NOT_FOUND = "No active code binding was found for the given code."
#: NFR-04/NFR-26: names nothing about what actually happened - this is a
#: platform invariant failure (see `CodeBindingWriteNotFoundError`'s own
#: docstring), not a caller mistake, so there is nothing for a caller to
#: act on differently. Deliberately does not say "try again" - unlike
#: every routine refusal in this module, a retry will not clear this one
#: (`_RESPONSE_500` in `catalogue_bindings.py` and `catalogue-write-api.md`
#: both say so too; this string must not contradict them).
_DETAIL_BINDING_WRITE_NOT_FOUND = (
    "This request could not be completed. Contact an administrator if the problem persists."
)
_DETAIL_PROPERTY_DEFINITION_DELETE_REFUSED = (
    "A property definition cannot be deleted. Deprecate it instead - deprecating retains "
    "every value already recorded against it."
)
_DETAIL_PROPERTY_KEY_IMMUTABLE = "A property's key cannot be changed once created."
_DETAIL_PROPERTY_ALREADY_DEPRECATED = "This property is already deprecated."
_DETAIL_PROPERTY_REACTIVATION_REFUSED = (
    "A deprecated property cannot be reactivated. Create a new property definition instead."
)
_DETAIL_SYSTEM_PROPERTY_DEPRECATION_REFUSED = "A built-in system property cannot be deprecated."
_DETAIL_PROPERTY_DEFINITION_KEY_EXISTS = "A property definition with this key already exists."
_DETAIL_DEPRECATED_PROPERTY_WRITE = (
    "This property has been deprecated and no longer accepts new values."
)
_DETAIL_PROPERTY_DATATYPE_UNKNOWN = "This is not a recognised property datatype."
_DETAIL_PROPERTY_CONSTRAINTS_INVALID = (
    "The constraints given for this property are not valid for its datatype."
)
_DETAIL_CONCEPT_NOT_FOUND = "No concept was found for this code in the AU edition."
#: Deliberately not `str(exc)`, and names no URL, variable or upstream host
#: (NFR-26) - see issue #240's own error-mapping table.
_DETAIL_TERMINOLOGY_UNAVAILABLE = (
    "The terminology server could not be reached. Try again shortly; the rest of "
    "this entry is unaffected."
)
_DETAIL_TERMINOLOGY_UPSTREAM = (
    "The terminology server's response could not be used. The problem has been logged."
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
        body = VersionConflictResponse(
            detail=_DETAIL_VERSION_CONFLICT,
            business_key=report.business_key,
            expected_row_version=report.expected_row_version,
            current_row_version=report.current_row_version,
            conflicts=[
                FieldConflictItem(
                    field=conflict.field,
                    submitted=conflict.submitted,
                    current=conflict.current,
                )
                for conflict in report.conflicts
            ],
            changed_by=report.changed_by,
            changed_at=report.changed_at,
        )
        return JSONResponse(
            status_code=EntryVersionConflictError.http_status,
            # `mode="json"` is what keeps `changed_at` an ISO-8601 string
            # rather than a `datetime` `JSONResponse` cannot encode - the
            # same serialisation the declared schema promises.
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(PreferredTermVersionRequiredError)
    async def _handle_preferred_term_version_required(
        _request: Request, exc: PreferredTermVersionRequiredError
    ) -> JSONResponse:
        # Logged as the class only: the message names the entry's own
        # preferred term, which is user-supplied free text (NFR-26/NFR-35).
        _logger.info("preferred-term amendment without a row version: %s", type(exc).__name__)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PREFERRED_TERM_VERSION_REQUIRED},
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

    @app.exception_handler(DesignationNotFoundError)
    async def _handle_designation_not_found(
        _request: Request, exc: DesignationNotFoundError
    ) -> JSONResponse:
        # issue #224: a term already retired, or never added, is simply not
        # addressable this way any more - not a caller mistake worth a
        # WARNING, matching CodeBindingNotFoundError's own precedent. Logged
        # as the class only, not `str(exc)`: the message quotes the
        # submitted term itself, user-supplied free text (NFR-26/NFR-35).
        _logger.info("designation not found: %s", type(exc).__name__)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_DESIGNATION_NOT_FOUND}
        )

    @app.exception_handler(DuplicateActiveTermError)
    async def _handle_duplicate_active_term(
        _request: Request, exc: DuplicateActiveTermError
    ) -> JSONResponse:
        # issue #224: logged as the class only, not `str(exc)` - the
        # message quotes the submitted term itself, user-supplied free text
        # exactly like a changelog note (NFR-26/NFR-35).
        _logger.info("designation refused, duplicate active term: %s", type(exc).__name__)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_DUPLICATE_ACTIVE_TERM}
        )

    @app.exception_handler(PreferredDesignationAlreadyActiveError)
    async def _handle_preferred_designation_already_active(
        _request: Request, exc: PreferredDesignationAlreadyActiveError
    ) -> JSONResponse:
        _logger.info("designation refused, preferred already active: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PREFERRED_DESIGNATION_ALREADY_ACTIVE},
        )

    @app.exception_handler(DesignationCollisionAcknowledgementConflictError)
    async def _handle_collision_acknowledgement_conflict(
        _request: Request, exc: DesignationCollisionAcknowledgementConflictError
    ) -> JSONResponse:
        _logger.info("collision acknowledgement refused, concurrent winner: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_COLLISION_ACKNOWLEDGEMENT_CONFLICT},
        )

    @app.exception_handler(PropertyValidationError)
    async def _handle_property_validation_error(
        _request: Request, exc: PropertyValidationError
    ) -> JSONResponse:
        # issue #52: a routine, expected refusal on a normal edit, not an
        # anomaly - INFO, not WARNING, matching every other field-level
        # validation refusal in this module. `issue.message` may echo a
        # submitted value back (e.g. "'Any' is not a valid value for
        # Specimen"), which is caller-supplied content the caller already
        # has - unlike a changelog note or search term, it is never
        # free-text the caller typed for someone else to read, so it is
        # safe to both log and return in full.
        _logger.info(
            "property value write refused: %s",
            [(issue.property_key, issue.code) for issue in exc.issues],
        )
        return JSONResponse(
            status_code=PropertyValidationError.http_status,
            content={
                "detail": _DETAIL_PROPERTY_VALIDATION,
                "issues": [
                    {
                        "property_key": issue.property_key,
                        "label": issue.label,
                        "code": issue.code,
                        "message": issue.message,
                        "ordinal": issue.ordinal,
                    }
                    for issue in exc.issues
                ],
            },
        )

    @app.exception_handler(PropertyDefinitionNotFoundError)
    async def _handle_property_definition_not_found(
        _request: Request, exc: PropertyDefinitionNotFoundError
    ) -> JSONResponse:
        _logger.info("property definition not found: %s", exc)
        return JSONResponse(
            status_code=PropertyDefinitionNotFoundError.http_status,
            content={"detail": _DETAIL_PROPERTY_DEFINITION_NOT_FOUND},
        )

    @app.exception_handler(PropertyNotCodeTypeError)
    async def _handle_property_not_code_type(
        _request: Request, exc: PropertyNotCodeTypeError
    ) -> JSONResponse:
        # issue #247: a routine, expected refusal - the caller named a real
        # property that just isn't datatype == "code", not an anomaly.
        _logger.info("property values refused, not a coded property: %s", exc)
        return JSONResponse(
            status_code=PropertyNotCodeTypeError.http_status,
            content={"detail": _DETAIL_PROPERTY_NOT_CODE_TYPE},
        )

    @app.exception_handler(PropertyValueSourceMisconfiguredError)
    async def _handle_property_value_source_misconfigured(
        _request: Request, exc: PropertyValueSourceMisconfiguredError
    ) -> JSONResponse:
        # issue #247: the property's own stored value_set_uri could not be
        # interpreted - a data-integrity fault in the definition, never a
        # caller mistake, matching `_handle_terminology_config_error`'s own
        # posture and detail string above.
        _logger.error("property values refused, value source misconfigured: %s", exc)
        return JSONResponse(
            status_code=PropertyValueSourceMisconfiguredError.http_status,
            content={"detail": _DETAIL_SERVER_MISCONFIGURED},
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
        body = DesignationCollisionResponse(
            detail=_DETAIL_DESIGNATION_COLLISION,
            collisions=[
                CollisionItem(
                    severity=c.severity.value,
                    business_key=c.business_key,
                    preferred_term=c.preferred_term,
                )
                for c in exc.collisions
            ],
        )
        return JSONResponse(status_code=exc.http_status, content=body.model_dump(mode="json"))

    @app.exception_handler(InvalidSCTIDError)
    async def _handle_invalid_sctid(_request: Request, exc: InvalidSCTIDError) -> JSONResponse:
        # issue #219: `nptc.catalogue.bindings.create_binding` calls
        # `SCTID(code)` before ever adding a row - a malformed or
        # Verhoeff-failing `code` is a routine, expected refusal on a
        # normal edit, not an anomaly. `nptc_shared.sctid.InvalidSCTIDError`
        # has no `http_status` ClassVar (it is a shared, non-API module),
        # so 422 is hardcoded here rather than read off the exception,
        # matching `TerminologyConfigError`'s handler above.
        _logger.info("SCTID refused: %s", type(exc).__name__)
        return JSONResponse(status_code=422, content={"detail": _DETAIL_INVALID_SCTID})

    @app.exception_handler(CodeBindingNotFoundError)
    async def _handle_code_binding_not_found(
        _request: Request, exc: CodeBindingNotFoundError
    ) -> JSONResponse:
        # Logged at INFO, matching `_handle_entry_not_found` above: a
        # caller addressing a code that has since been retired or never
        # bound is an ordinary event on an editing surface, not an anomaly.
        _logger.info("code binding not found: %s", exc)
        return JSONResponse(
            status_code=CodeBindingNotFoundError.http_status,
            content={"detail": _DETAIL_BINDING_NOT_FOUND},
        )

    @app.exception_handler(CodeBindingAlreadyRetiredError)
    async def _handle_code_binding_already_retired(
        _request: Request, exc: CodeBindingAlreadyRetiredError
    ) -> JSONResponse:
        _logger.info("retire refused, binding already retired: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_ALREADY_RETIRED}
        )

    @app.exception_handler(CodeBindingAlreadyActiveError)
    async def _handle_code_binding_already_active(
        _request: Request, exc: CodeBindingAlreadyActiveError
    ) -> JSONResponse:
        # FR-08: the entry side of "at most one active binding" - see
        # `CodeBindingCodeAlreadyBoundError`'s handler below for the code
        # side. Logged at INFO: a routine, expected refusal on a normal
        # edit, not an anomaly.
        _logger.info("bind refused, entry already has an active binding: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_ALREADY_ACTIVE}
        )

    @app.exception_handler(CodeBindingCodeAlreadyBoundError)
    async def _handle_code_binding_code_already_bound(
        _request: Request, exc: CodeBindingCodeAlreadyBoundError
    ) -> JSONResponse:
        # issue #49's blocking severity, the code side of FR-08's "one
        # active binding" - see `CodeBindingAlreadyActiveError`'s handler
        # above. The exception message names the other entry's internal
        # id; the response body never does (NFR-04), matching this
        # module's own detail-string convention.
        _logger.info("bind refused, code already actively bound elsewhere: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_CODE_ALREADY_BOUND}
        )

    @app.exception_handler(CodeBindingNotRetiredError)
    async def _handle_code_binding_not_retired(
        _request: Request, exc: CodeBindingNotRetiredError
    ) -> JSONResponse:
        _logger.info("replace refused, superseded binding is not retired: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_NOT_RETIRED}
        )

    @app.exception_handler(CodeBindingSelfSupersessionError)
    async def _handle_code_binding_self_supersession(
        _request: Request, exc: CodeBindingSelfSupersessionError
    ) -> JSONResponse:
        _logger.info("replace refused, self-supersession: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_SELF_SUPERSESSION}
        )

    @app.exception_handler(InvalidCodeBindingEditionHintError)
    async def _handle_invalid_code_binding_edition_hint(
        _request: Request, exc: InvalidCodeBindingEditionHintError
    ) -> JSONResponse:
        _logger.info("edition hint refused: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_INVALID_EDITION_HINT}
        )

    @app.exception_handler(InvalidCodeBindingSystemError)
    async def _handle_invalid_code_binding_system(
        _request: Request, exc: InvalidCodeBindingSystemError
    ) -> JSONResponse:
        _logger.info("code system refused: %s", exc)
        return JSONResponse(status_code=exc.http_status, content={"detail": _DETAIL_INVALID_SYSTEM})

    @app.exception_handler(CodeBindingWriteNotFoundError)
    async def _handle_code_binding_write_not_found(
        _request: Request, exc: CodeBindingWriteNotFoundError
    ) -> JSONResponse:
        # ERROR, not INFO: unlike every other handler in this module, this
        # one is never a caller mistake - see the exception's own
        # docstring. Reaching here means a route's own re-read-after-write
        # invariant broke, which is worth paging on, not just tracing.
        _logger.error("code binding write could not be verified: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_BINDING_WRITE_NOT_FOUND}
        )

    @app.exception_handler(PropertyDefinitionDeleteRefusedError)
    async def _handle_property_definition_delete_refused(
        _request: Request, exc: PropertyDefinitionDeleteRefusedError
    ) -> JSONResponse:
        _logger.info("property definition delete refused: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PROPERTY_DEFINITION_DELETE_REFUSED},
        )

    @app.exception_handler(PropertyKeyImmutableError)
    async def _handle_property_key_immutable(
        _request: Request, exc: PropertyKeyImmutableError
    ) -> JSONResponse:
        _logger.info("property key amendment refused: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_PROPERTY_KEY_IMMUTABLE}
        )

    @app.exception_handler(PropertyAlreadyDeprecatedError)
    async def _handle_property_already_deprecated(
        _request: Request, exc: PropertyAlreadyDeprecatedError
    ) -> JSONResponse:
        _logger.info("deprecate refused, already deprecated: %s", exc)
        return JSONResponse(
            status_code=exc.http_status, content={"detail": _DETAIL_PROPERTY_ALREADY_DEPRECATED}
        )

    @app.exception_handler(PropertyReactivationRefusedError)
    async def _handle_property_reactivation_refused(
        _request: Request, exc: PropertyReactivationRefusedError
    ) -> JSONResponse:
        _logger.info("reactivation refused: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PROPERTY_REACTIVATION_REFUSED},
        )

    @app.exception_handler(SystemPropertyDeprecationRefusedError)
    async def _handle_system_property_deprecation_refused(
        _request: Request, exc: SystemPropertyDeprecationRefusedError
    ) -> JSONResponse:
        _logger.info("deprecate refused, system property: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_SYSTEM_PROPERTY_DEPRECATION_REFUSED},
        )

    @app.exception_handler(PropertyDefinitionKeyExistsError)
    async def _handle_property_definition_key_exists(
        _request: Request, exc: PropertyDefinitionKeyExistsError
    ) -> JSONResponse:
        _logger.info("create refused, key already exists: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PROPERTY_DEFINITION_KEY_EXISTS},
        )

    @app.exception_handler(DeprecatedPropertyWriteError)
    async def _handle_deprecated_property_write(
        _request: Request, exc: DeprecatedPropertyWriteError
    ) -> JSONResponse:
        # issue #223 review finding 11: the response body carries only
        # `detail`, matching every other handler in this module - the
        # `property_key` stays in this log line only, which already has it.
        _logger.info(
            "value write refused, property deprecated: %s (property_key=%s)",
            exc,
            exc.property_key,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_DEPRECATED_PROPERTY_WRITE},
        )

    @app.exception_handler(PropertyDatatypeUnknownError)
    async def _handle_property_datatype_unknown(
        _request: Request, exc: PropertyDatatypeUnknownError
    ) -> JSONResponse:
        _logger.info("property write refused, unknown datatype: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PROPERTY_DATATYPE_UNKNOWN},
        )

    @app.exception_handler(PropertyConstraintsInvalidError)
    async def _handle_property_constraints_invalid(
        _request: Request, exc: PropertyConstraintsInvalidError
    ) -> JSONResponse:
        _logger.info("property write refused, invalid constraints: %s", exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_PROPERTY_CONSTRAINTS_INVALID},
        )

    @app.exception_handler(ConceptNotFoundError)
    async def _handle_concept_not_found(
        _request: Request, exc: ConceptNotFoundError
    ) -> JSONResponse:
        # FR-26: a routine, expected outcome of a caller typing a code the
        # server does not have - INFO, not WARNING, matching every other
        # "not found" handler in this module.
        _logger.info("concept lookup refused, not found: %s", exc)
        return JSONResponse(
            status_code=ConceptNotFoundError.http_status,
            content={"detail": _DETAIL_CONCEPT_NOT_FOUND},
        )

    @app.exception_handler(TerminologyUnavailableError)
    async def _handle_terminology_unavailable(
        _request: Request, exc: TerminologyUnavailableError
    ) -> JSONResponse:
        # WARNING, not INFO: unlike a caller mistake, this is the shared
        # terminology server misbehaving or unreachable - worth noticing,
        # not routine. FR-54: nothing here degrades a result, it only tells
        # the caller the live check could not run this time.
        _logger.warning("terminology lookup refused, server unavailable: %s", exc)
        # `is not None`, not truthiness: a server-supplied `retry_after` of
        # `0.0` is a real value ("retry immediately"), not "none given" -
        # truthiness would silently drop the header for it. `ceil` rounds
        # up rather than `int`'s truncate-toward-zero, so a sub-second value
        # (e.g. `0.4`) is never rounded down to `Retry-After: 0`, and
        # `max(1, ...)` is the floor RFC 9110 implies for a delay worth
        # sending at all - it also guards against an ever-negative value
        # producing an invalid header.
        headers = (
            {"Retry-After": str(max(1, math.ceil(exc.retry_after)))}
            if exc.retry_after is not None
            else None
        )
        return JSONResponse(
            status_code=TerminologyUnavailableError.http_status,
            content={"detail": _DETAIL_TERMINOLOGY_UNAVAILABLE},
            headers=headers,
        )

    @app.exception_handler(TerminologyUpstreamError)
    async def _handle_terminology_upstream(
        _request: Request, exc: TerminologyUpstreamError
    ) -> JSONResponse:
        # ERROR, not WARNING: an unparseable or otherwise unusable response
        # from a conformant endpoint is a defect worth investigating, not
        # an ordinary outage - see this exception's own docstring for why
        # this is the catch-all rather than a 404.
        _logger.error("terminology lookup refused, unusable response: %s", exc)
        return JSONResponse(
            status_code=TerminologyUpstreamError.http_status,
            content={"detail": _DETAIL_TERMINOLOGY_UPSTREAM},
        )
