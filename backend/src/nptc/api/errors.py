"""Mapping the auth and domain error families to HTTP responses.

Two auth families, kept apart upstream and kept apart here:

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
asserts exactly this. The detail strings below are fixed, client-facing
sentences, never ``str(exc)``: exception messages are diagnostic and do
mention roles and UUIDs, which is correct for a log and wrong for a
response.

Most refusals are a row of ``_REFUSALS``. A refusal that builds its own body
or branches on the instance is a function in ``register_exception_handlers``.
A constraint that its name alone cannot disambiguate (a malformed
designation ``use``, ``ck_designation_no_en_au_preferred``) is not mapped
here: it is refused as a pydantic 422 at the request-body layer, before the
ORM is touched (``nptc.api.routers.catalogue_designations``).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import CredentialRequiredError, MalformedAuthorizationError
from nptc.api.labels import AU_PREFERRED_TERM_PROVENANCE, LabelProvenance
from nptc.audit.queries import (
    AuditFilterError,
    EntityIdRequiresEntityTypeError,
    MalformedAuditCursorError,
)
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
from nptc.catalogue.code_systems import REGISTERED_TOKENS_DETAIL
from nptc.catalogue.collisions import (
    DesignationCollisionAcknowledgementConflictError,
    DesignationCollisionError,
)
from nptc.catalogue.designations import (
    DesignationAlreadyRetiredError,
    DesignationNotFoundError,
    DesignationNotRetiredError,
    DuplicateActiveTermError,
    PreferredDesignationAlreadyActiveError,
)
from nptc.catalogue.errors import (
    CodeLookupNotFoundError,
    ConflictReport,
    EntryNotFoundError,
    EntryVersionConflictError,
)
from nptc.catalogue.facets import FilterRefusedError
from nptc.catalogue.history import MalformedHistoryCursorError
from nptc.catalogue.local_codes import (
    InvalidLocalCodeSystemKeyError,
    InvalidMatchStrengthError,
    LocalCodeAlreadyDeprecatedError,
    LocalCodeSystemAlreadyDeprecatedError,
)
from nptc.catalogue.maintenance import MalformedListingCursorError
from nptc.catalogue.property_value_sources import (
    PropertyNotCodeTypeError,
    PropertyValueSelectionConflictError,
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
from nptc.registry.handlers import UnknownDatatypeError
from nptc.settings import AuthSettings
from nptc.terminology.errors import (
    ConceptNotFoundError,
    TerminologyUnavailableError,
    TerminologyUpstreamError,
)
from nptc_shared.sctid import InvalidSCTIDError
from nptc_shared.terminology import TerminologyConfigError

_logger = logging.getLogger(__name__)


# --- response bodies that carry more than `detail` ------------------------
#
# Most refusals are an `ErrorResponse`: one sentence and nothing else. FR-38's
# conflicting values (409), FR-05's colliding entry (409) and FR-09's
# field-level issues (422) are not, because a bare sentence would withhold
# exactly what the requirement exists to give the caller.
#
# Each is a model that the handler builds its body through, so a router naming
# it in `responses=` puts the real shape in `docs/api/openapi.json`. Building
# through the model stops the declared schema and the emitted body drifting,
# which a hand-written `content` block next to a hand-built `dict` invites.


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


def version_conflict_response(report: ConflictReport) -> VersionConflictResponse:
    """Builds FR-38's 409 body from a domain `ConflictReport` - the one
    place that shape gets built, shared by `_handle_entry_version_conflict`
    (a single stale save, the whole request) and the bulk property-value route
    (one outcome among many, never the whole response's status)."""
    return VersionConflictResponse(
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


class CollisionItem(BaseModel):
    """One FR-05 collision: the live entry a submitted term collides with,
    named by its public identifier and preferred term - never its internal
    id (NFR-04/NFR-26).

    `label_provenance["preferred_term"]` is always `AU_PREFERRED_TERM_
    PROVENANCE` (FR-98): `preferred_term` here is the
    *colliding* entry's own catalogue preferred term, the identical field
    and designation type as `EntrySummary.preferred_term` - matching
    `CollisionWarning`'s own reasoning for its own `preferred_term` field
    (`nptc.api.routers.catalogue_designations`), the 200-path twin of this
    409 body.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    business_key: str
    preferred_term: str
    label_provenance: dict[str, LabelProvenance]


class DesignationCollisionResponse(BaseModel):
    """FR-05's 409 body. PRD SS17.2 item 5 is explicit that the refusal names
    the colliding entry rather than returning a bare status, so an editor
    can go and look at it."""

    model_config = ConfigDict(frozen=True)

    detail: str
    collisions: list[CollisionItem]


class PropertyIssueItem(BaseModel):
    """One field-level problem with an attempted property-value write - the
    wire shape of `nptc.catalogue.property_values.PropertyWriteIssue`.
    `ordinal` is `None` for a cardinality issue that applies to the
    property as a whole rather than one value in it."""

    model_config = ConfigDict(frozen=True)

    property_key: str
    label: str
    code: str
    message: str
    ordinal: int | None


class PropertyValidationResponse(BaseModel):
    """FR-09/FR-10/FR-88/FR-89's 422 body: `PropertyValidationError`'s
    `issues[]`, declared as a model rather than a hand-built dict - a
    router naming this in its `responses=` puts the real `issues[]` shape
    in `docs/api/openapi.json`, matching `VersionConflictResponse`/
    `DesignationCollisionResponse`'s own precedent, so a generated client
    types the field-level detail instead of a bare `{detail}`."""

    model_config = ConfigDict(frozen=True)

    detail: str
    issues: list[PropertyIssueItem]


def _step_up_challenge(mfa_acr_values: frozenset[str]) -> str:
    """RFC 9470 step-up challenge, pre-specified in
    docs/architecture/permissions.md. `acr_values` comes from
    `AuthSettings.mfa_acr_values` (`NPTC_MFA_ACR_VALUES`) rather than a
    literal `"2"`, so changing the realm's LoA map needs no code change here.
    Space-joined per RFC 9470 and sorted, so the header is deterministic
    across runs of a `frozenset`.
    """
    return f'Bearer error="insufficient_user_authentication", acr_values="{" ".join(sorted(mfa_acr_values))}"'


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
#: FR-17: one shared 404 sentence for both an unregistered `system_token`/URI
#: and a registered one with no matching published entry (see
#: `nptc.catalogue.code_systems`'s module docstring). Sourced from the registry
#: so a second registered alias updates this text for free.
_DETAIL_CODE_LOOKUP_NOT_FOUND = REGISTERED_TOKENS_DETAIL
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
#: Shared by two addressing conventions: add, amend and retire address a
#: designation by its currently-*active* term, reinstate by its currently-
#: *retired* term. The sentence omits the word "active" so it is not misleading
#: on either route.
_DETAIL_DESIGNATION_NOT_FOUND = "No matching designation was found for the given term."
_DETAIL_DUPLICATE_ACTIVE_TERM = (
    "This entry already has an active designation for this term, once case, spacing "
    "and punctuation are ignored."
)
#: Reinstating a term that already has an active designation: it was never
#: retired, was already reinstated, or was retired and re-added as a synonym.
_DETAIL_DESIGNATION_NOT_RETIRED = (
    "This term is already active on this entry, so there is nothing to reinstate."
)
_DETAIL_PREFERRED_DESIGNATION_ALREADY_ACTIVE = (
    "This entry already has an active preferred term in this language."
)
_DETAIL_COLLISION_ACKNOWLEDGEMENT_CONFLICT = (
    "This collision was just acknowledged by another request. No further action is needed."
)
_DETAIL_SEARCH_QUERY_EMPTY = "Enter something to search for."
_DETAIL_SEARCH_CURSOR = (
    "This page cursor is not one this API issued, or it was issued for a different "
    "search. Pass a `next_cursor` value back unmodified alongside the same query and "
    "filters, or start again from the first page."
)
#: Also served for `MalformedAuditCursorError` (its row in `_REFUSALS`):
#: `nptc.api.routers.audit.AuditCursorQuery` copies `nptc.catalogue.history`'s
#: cursor shape verbatim, so the refusal reads the same way.
_DETAIL_HISTORY_CURSOR = (
    "This page cursor is not one this API issued. Pass a `next_cursor` value back "
    "unmodified, or start again from the first page."
)
#: Mirrors `_DETAIL_SEARCH_CURSOR`, with `sort` in place of a search query.
_DETAIL_LISTING_CURSOR = (
    "This page cursor is not one this API issued, or it was issued for a different "
    "sort or filter set. Pass a `next_cursor` value back unmodified alongside the "
    "same `sort` and filters, or start again from the first page."
)
_DETAIL_ENTITY_ID_REQUIRES_ENTITY_TYPE = "The `entity_id` filter requires `entity_type` as well."
_DETAIL_OCCURRED_RANGE_INVALID = "`occurred_from` must be strictly before `occurred_to`."
#: FR-16. Names no property key and no value: the parameter is caller-supplied
#: text on a public, unauthenticated endpoint (NFR-26/NFR-35), and which
#: properties exist but are not offered as filters is editorial state this
#: surface has no business disclosing. The remedy is the same for every member
#: of the family, which is why they share one sentence - `GET
#: /catalogue/search` returns the facets that *are* available, with their keys.
_DETAIL_FILTER_REFUSED = (
    "One of the `filter.` parameters is not one this endpoint accepts. Use a facet "
    "key from the `facets` list on a search response, an operator that facet "
    "supports, a value of the right kind, and no more values in one facet's "
    "selection than that facet's own limit allows."
)
#: Deliberately not "an internal error occurred": FR-83's refusal is a data
#: defect on one binding, and naming the kind of defect lets the caller report
#: something an administrator can act on. It names no internal identifier,
#: served label or stored value; the business key the caller sent identifies
#: the entry.
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
_DETAIL_PROPERTY_VALUE_SELECTION_CONFLICT = (
    "`code` cannot be combined with `filter`, `offset`, or `count`. Resolve specific "
    "codes with `code` alone, or page through the offerable values with the other "
    "parameters."
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
#: NFR-04/NFR-26: names nothing about what happened. This is a platform
#: invariant failure (see `CodeBindingWriteNotFoundError`), not a caller
#: mistake. It deliberately does not say "try again", because a retry will not
#: clear it; `_RESPONSE_500` in `catalogue_bindings.py` and
#: `catalogue-write-api.md` say the same, and this string must not contradict
#: them.
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
#: (NFR-26). The error-mapping table is in docs/architecture/terminology-client.md.
_DETAIL_TERMINOLOGY_UNAVAILABLE = (
    "The terminology server could not be reached. Try again shortly; the rest of "
    "this entry is unaffected."
)
_DETAIL_TERMINOLOGY_UPSTREAM = (
    "The terminology server's response could not be used. The problem has been logged."
)
_DETAIL_LOCAL_CODE_SYSTEM_ALREADY_DEPRECATED = "This local code system is already deprecated."
_DETAIL_LOCAL_CODE_ALREADY_DEPRECATED = "This local code is already deprecated."
_DETAIL_INVALID_LOCAL_CODE_SYSTEM_KEY = (
    "A local code system key is 1 to 63 characters: a lowercase letter, then lowercase "
    "letters, digits or underscores."
)
_DETAIL_INVALID_MATCH_STRENGTH = (
    "The match strength must be one of: exact, narrower, broader, ambiguous."
)


#: WWW-Authenticate on a 401 and never on a 403 - the pair endpoints most
#: reliably get backwards (`assert_http_forbidden` checks for it).
_BEARER_CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}


class _HasHttpStatus(Protocol):
    http_status: int


def _exc(exc: Exception) -> tuple[object, ...]:
    return (exc,)


def _name(exc: Exception) -> tuple[object, ...]:
    return (type(exc).__name__,)


def _name_and_exc(exc: Exception) -> tuple[object, ...]:
    return (type(exc).__name__, exc)


@dataclass(frozen=True)
class _Refusal:
    """One plain refusal: a fixed client-facing `detail` and one log line.

    `log_message=None` logs nothing. `status=None` reads `exc.http_status`
    off the raised instance; a row sets `status` only for a class that
    carries none.
    """

    detail: str
    log_message: str | None
    log_args: Callable[[Exception], tuple[object, ...]] = _exc
    log_level: int = logging.INFO
    status: int | None = None
    headers: Mapping[str, str] | None = None


def _handler_for(refusal: _Refusal) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        if refusal.log_message is not None:
            _logger.log(refusal.log_level, refusal.log_message, *refusal.log_args(exc))
        status = (
            refusal.status
            if refusal.status is not None
            else cast("_HasHttpStatus", exc).http_status
        )
        return JSONResponse(
            status_code=status, content={"detail": refusal.detail}, headers=refusal.headers
        )

    return handle


# --- the plain refusals: one row each --------------------------------------
#
# Starlette serves a class from the row of its nearest registered base, so a
# subclass needs no row of its own. Every class under `nptc` or `nptc_shared`
# that carries an `http_status` must resolve to a row or to a function in
# `register_exception_handlers` (`test_api_error_table.py` enforces it).
#
# A row logs at INFO unless it says otherwise: these are ordinary, expected
# refusals. Where a row logs the class name alone (`_name`), the exception
# message quotes caller-supplied text - a cursor, a term, a note, a filter -
# and so stays out of the log (NFR-26, NFR-35). The response never carries
# `str(exc)`.
#
# A refusal that builds its own body, or branches on the instance, is a
# function in `register_exception_handlers` instead.

_REFUSALS: Final[dict[type[Exception], _Refusal]] = {
    # 401: we could not establish who you are. An expired token is the most
    # common event on an authenticated API, so INFO. The message may name the
    # issuer or audience, never the token.
    TokenError: _Refusal(
        _DETAIL_UNAUTHENTICATED,
        "token refused: %s: %s",
        _name_and_exc,
        status=401,
        headers=_BEARER_CHALLENGE,
    ),
    MalformedAuthorizationError: _Refusal(
        _DETAIL_UNAUTHENTICATED,
        "authorization header refused: %s",
        status=401,
        headers=_BEARER_CHALLENGE,
    ),
    CredentialRequiredError: _Refusal(
        _DETAIL_SIGN_IN_REQUIRED,
        "credential required: %s",
        status=401,
        headers=_BEARER_CHALLENGE,
    ),
    EntryNotFoundError: _Refusal(_DETAIL_NOT_FOUND, "entry not found: %s"),
    CodeLookupNotFoundError: _Refusal(_DETAIL_CODE_LOOKUP_NOT_FOUND, "code lookup not found: %s"),
    # Not logged: a blank search box on a public, unauthenticated endpoint is
    # the most ordinary client mistake there is, and logging it invites
    # filling the log with someone else's traffic.
    EmptySearchQueryError: _Refusal(_DETAIL_SEARCH_QUERY_EMPTY, None),
    MalformedSearchCursorError: _Refusal(_DETAIL_SEARCH_CURSOR, "search cursor refused: %s", _name),
    MalformedHistoryCursorError: _Refusal(
        _DETAIL_HISTORY_CURSOR, "history cursor refused: %s", _name
    ),
    MalformedListingCursorError: _Refusal(
        _DETAIL_LISTING_CURSOR, "listing cursor refused: %s", _name
    ),
    MalformedAuditCursorError: _Refusal(_DETAIL_HISTORY_CURSOR, "audit cursor refused: %s", _name),
    # Refused, never ignored: a filter the server dropped silently serves a
    # page that looks like an answer to the question asked and answers a
    # different one. The class tells an unknown key from a non-filterable one
    # from a bad operator - worth having in a log, not in a response.
    FilterRefusedError: _Refusal(_DETAIL_FILTER_REFUSED, "filter refused: %s", _name),
    # A safety net for paths that bypass `create_app`'s eager terminology-client
    # build (a test app, a dependency override). The bad value goes to the log,
    # never the response (NFR-26).
    TerminologyConfigError: _Refusal(
        _DETAIL_SERVER_MISCONFIGURED,
        "terminology configuration refused: %s",
        log_level=logging.ERROR,
        status=500,
    ),
    # WARNING: unlike every other caller-facing refusal here, this is not a
    # caller mistake. FR-82 guarantees every stored `fsn` came from the
    # terminology server, so reaching here means a published entry broke that
    # guarantee. Blanking the label and serving a 200 would hide it (FR-83).
    # Both classes share one sentence: a caller can act on neither differently.
    NotAServedFSNError: _Refusal(
        _DETAIL_DISPLAY_TERM,
        "display term could not be rendered: %s: %s",
        _name_and_exc,
        log_level=logging.WARNING,
    ),
    EmptyDisplayTermError: _Refusal(
        _DETAIL_DISPLAY_TERM,
        "display term could not be rendered: %s: %s",
        _name_and_exc,
        log_level=logging.WARNING,
    ),
    ChangelogNoteError: _Refusal(_DETAIL_CHANGELOG_NOTE, "changelog note refused: %s", _name),
    TermCleaningError: _Refusal(_DETAIL_TERM_CLEANING, "term refused: %s", _name),
    DesignationLanguageError: _Refusal(
        _DETAIL_DESIGNATION_LANGUAGE, "designation language tag refused: %s"
    ),
    DesignationAlreadyRetiredError: _Refusal(
        _DETAIL_ALREADY_RETIRED, "retire refused, already retired: %s"
    ),
    DesignationNotFoundError: _Refusal(
        _DETAIL_DESIGNATION_NOT_FOUND, "designation not found: %s", _name
    ),
    DesignationNotRetiredError: _Refusal(
        _DETAIL_DESIGNATION_NOT_RETIRED, "reinstatement refused, not retired: %s", _name
    ),
    DuplicateActiveTermError: _Refusal(
        _DETAIL_DUPLICATE_ACTIVE_TERM, "designation refused, duplicate active term: %s", _name
    ),
    PreferredDesignationAlreadyActiveError: _Refusal(
        _DETAIL_PREFERRED_DESIGNATION_ALREADY_ACTIVE,
        "designation refused, preferred already active: %s",
    ),
    DesignationCollisionAcknowledgementConflictError: _Refusal(
        _DETAIL_COLLISION_ACKNOWLEDGEMENT_CONFLICT,
        "collision acknowledgement refused, concurrent winner: %s",
    ),
    PropertyDefinitionNotFoundError: _Refusal(
        _DETAIL_PROPERTY_DEFINITION_NOT_FOUND, "property definition not found: %s"
    ),
    PropertyNotCodeTypeError: _Refusal(
        _DETAIL_PROPERTY_NOT_CODE_TYPE, "property values refused, not a coded property: %s"
    ),
    PropertyValueSelectionConflictError: _Refusal(
        _DETAIL_PROPERTY_VALUE_SELECTION_CONFLICT,
        "property values refused, conflicting selection: %s",
    ),
    # ERROR: the property's own stored `value_set_uri` could not be
    # interpreted - a data fault in the definition, never a caller mistake.
    PropertyValueSourceMisconfiguredError: _Refusal(
        _DETAIL_SERVER_MISCONFIGURED,
        "property values refused, value source misconfigured: %s",
        log_level=logging.ERROR,
    ),
    # `nptc_shared` carries no `http_status`: it is a shared, non-API module.
    InvalidSCTIDError: _Refusal(_DETAIL_INVALID_SCTID, "SCTID refused: %s", _name, status=422),
    CodeBindingNotFoundError: _Refusal(_DETAIL_BINDING_NOT_FOUND, "code binding not found: %s"),
    CodeBindingAlreadyRetiredError: _Refusal(
        _DETAIL_BINDING_ALREADY_RETIRED, "retire refused, binding already retired: %s"
    ),
    CodeBindingAlreadyActiveError: _Refusal(
        _DETAIL_BINDING_ALREADY_ACTIVE, "bind refused, entry already has an active binding: %s"
    ),
    # The message names the other entry's internal id; the response never
    # does (NFR-04).
    CodeBindingCodeAlreadyBoundError: _Refusal(
        _DETAIL_BINDING_CODE_ALREADY_BOUND,
        "bind refused, code already actively bound elsewhere: %s",
    ),
    CodeBindingNotRetiredError: _Refusal(
        _DETAIL_BINDING_NOT_RETIRED, "replace refused, superseded binding is not retired: %s"
    ),
    CodeBindingSelfSupersessionError: _Refusal(
        _DETAIL_BINDING_SELF_SUPERSESSION, "replace refused, self-supersession: %s"
    ),
    InvalidCodeBindingEditionHintError: _Refusal(
        _DETAIL_INVALID_EDITION_HINT, "edition hint refused: %s"
    ),
    InvalidCodeBindingSystemError: _Refusal(_DETAIL_INVALID_SYSTEM, "code system refused: %s"),
    # ERROR: never a caller mistake. A route's own re-read-after-write
    # invariant broke, which is worth paging on, not just tracing.
    CodeBindingWriteNotFoundError: _Refusal(
        _DETAIL_BINDING_WRITE_NOT_FOUND,
        "code binding write could not be verified: %s",
        log_level=logging.ERROR,
    ),
    PropertyDefinitionDeleteRefusedError: _Refusal(
        _DETAIL_PROPERTY_DEFINITION_DELETE_REFUSED, "property definition delete refused: %s"
    ),
    PropertyKeyImmutableError: _Refusal(
        _DETAIL_PROPERTY_KEY_IMMUTABLE, "property key amendment refused: %s"
    ),
    PropertyAlreadyDeprecatedError: _Refusal(
        _DETAIL_PROPERTY_ALREADY_DEPRECATED, "deprecate refused, already deprecated: %s"
    ),
    PropertyReactivationRefusedError: _Refusal(
        _DETAIL_PROPERTY_REACTIVATION_REFUSED, "reactivation refused: %s"
    ),
    SystemPropertyDeprecationRefusedError: _Refusal(
        _DETAIL_SYSTEM_PROPERTY_DEPRECATION_REFUSED, "deprecate refused, system property: %s"
    ),
    PropertyDefinitionKeyExistsError: _Refusal(
        _DETAIL_PROPERTY_DEFINITION_KEY_EXISTS, "create refused, key already exists: %s"
    ),
    PropertyDatatypeUnknownError: _Refusal(
        _DETAIL_PROPERTY_DATATYPE_UNKNOWN, "property write refused, unknown datatype: %s"
    ),
    PropertyConstraintsInvalidError: _Refusal(
        _DETAIL_PROPERTY_CONSTRAINTS_INVALID, "property write refused, invalid constraints: %s"
    ),
    # The read-path twin of `PropertyDatatypeUnknownError`. That one is a
    # caller-supplied `datatype` that does not resolve, a 422 on a write.
    # This one is an already-stored definition naming a datatype this
    # process's registry no longer knows: the request was well formed and the
    # server-side state has drifted. ERROR, because the endpoint now fails for
    # every caller until somebody looks.
    UnknownDatatypeError: _Refusal(
        _DETAIL_SERVER_MISCONFIGURED,
        "read refused, definition names an unregistered datatype: %s",
        log_level=logging.ERROR,
        status=500,
    ),
    LocalCodeSystemAlreadyDeprecatedError: _Refusal(
        _DETAIL_LOCAL_CODE_SYSTEM_ALREADY_DEPRECATED,
        "deprecate refused, local code system already deprecated: %s",
    ),
    LocalCodeAlreadyDeprecatedError: _Refusal(
        _DETAIL_LOCAL_CODE_ALREADY_DEPRECATED,
        "deprecate refused, local code already deprecated: %s",
    ),
    InvalidLocalCodeSystemKeyError: _Refusal(
        _DETAIL_INVALID_LOCAL_CODE_SYSTEM_KEY, "local code system key refused: %s", _name
    ),
    InvalidMatchStrengthError: _Refusal(
        _DETAIL_INVALID_MATCH_STRENGTH, "match strength refused: %s", _name
    ),
    ConceptNotFoundError: _Refusal(
        _DETAIL_CONCEPT_NOT_FOUND, "concept lookup refused, not found: %s"
    ),
    # ERROR: an unusable response from a conformant endpoint is a defect worth
    # investigating, not an ordinary outage. See the exception's own docstring
    # for why this is the catch-all rather than a 404.
    TerminologyUpstreamError: _Refusal(
        _DETAIL_TERMINOLOGY_UPSTREAM,
        "terminology lookup refused, unusable response: %s",
        log_level=logging.ERROR,
    ),
}


def register_exception_handlers(app: FastAPI, auth_settings: AuthSettings) -> None:
    for exc_class, refusal in _REFUSALS.items():
        app.add_exception_handler(exc_class, _handler_for(refusal))

    step_up_challenge = _step_up_challenge(auth_settings.mfa_acr_values)

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
                headers={"WWW-Authenticate": step_up_challenge},
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
        body = version_conflict_response(exc.report)
        return JSONResponse(
            status_code=EntryVersionConflictError.http_status,
            # `mode="json"` is what keeps `changed_at` an ISO-8601 string
            # rather than a `datetime` `JSONResponse` cannot encode - the
            # same serialisation the declared schema promises.
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(AuditFilterError)
    async def _handle_audit_filter_error(_request: Request, exc: AuditFilterError) -> JSONResponse:
        # Discriminated by subclass, unlike most handlers here, because it is
        # safe: `AuditFilterError`'s two subclasses carry a fixed message with no
        # caller input folded in, so naming the reason carries none of the
        # NFR-26/NFR-35 risk a cursor or search-term message would.
        _logger.info("audit filter refused: %s", type(exc).__name__)
        detail = (
            _DETAIL_ENTITY_ID_REQUIRES_ENTITY_TYPE
            if isinstance(exc, EntityIdRequiresEntityTypeError)
            else _DETAIL_OCCURRED_RANGE_INVALID
        )
        return JSONResponse(status_code=AuditFilterError.http_status, content={"detail": detail})

    @app.exception_handler(PropertyValidationError)
    async def _handle_property_validation_error(
        _request: Request, exc: PropertyValidationError
    ) -> JSONResponse:
        # A routine refusal on a normal edit: INFO, like every other field-level
        # validation refusal here. `issue.message` may echo a submitted value
        # (e.g. "'Any' is not a valid value for Specimen"), which the caller
        # already has and is not free text typed for someone else to read, so it
        # is safe to both log and return in full.
        _logger.info(
            "property value write refused: %s",
            [(issue.property_key, issue.code) for issue in exc.issues],
        )
        body = PropertyValidationResponse(
            detail=_DETAIL_PROPERTY_VALIDATION,
            issues=[
                PropertyIssueItem(
                    property_key=issue.property_key,
                    label=issue.label,
                    code=issue.code,
                    message=issue.message,
                    ordinal=issue.ordinal,
                )
                for issue in exc.issues
            ],
        )
        return JSONResponse(
            status_code=PropertyValidationError.http_status,
            content=body.model_dump(mode="json"),
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
                    label_provenance={"preferred_term": AU_PREFERRED_TERM_PROVENANCE},
                )
                for c in exc.collisions
            ],
        )
        return JSONResponse(status_code=exc.http_status, content=body.model_dump(mode="json"))

    @app.exception_handler(DeprecatedPropertyWriteError)
    async def _handle_deprecated_property_write(
        _request: Request, exc: DeprecatedPropertyWriteError
    ) -> JSONResponse:
        # The body carries only `detail`; `property_key` stays in this log line.
        _logger.info(
            "value write refused, property deprecated: %s (property_key=%s)",
            exc,
            exc.property_key,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": _DETAIL_DEPRECATED_PROPERTY_WRITE},
        )

    @app.exception_handler(TerminologyUnavailableError)
    async def _handle_terminology_unavailable(
        _request: Request, exc: TerminologyUnavailableError
    ) -> JSONResponse:
        # WARNING, not INFO: the shared terminology server is misbehaving or
        # unreachable, which is worth noticing. FR-54: nothing here degrades a
        # result; it tells the caller the live check could not run this time.
        _logger.warning("terminology lookup refused, server unavailable: %s", exc)
        # `is not None`, not truthiness: a `retry_after` of `0.0` is a real value
        # ("retry immediately"). `ceil` rather than truncation, so `0.4` never
        # becomes `Retry-After: 0`; `max(1, ...)` is the floor for a delay worth
        # sending and keeps a negative value from producing an invalid header.
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
