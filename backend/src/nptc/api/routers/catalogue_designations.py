"""Write routes over `designation` (FR-04, FR-05, FR-36, FR-37, NFR-08).

`docs/architecture/catalogue-write-api.md` ("Designations") records the reasoning behind
the points below. Domain exceptions carry `http_status` and `nptc.api.errors` maps them, so
no route body has a try/except.

**A separate router from `catalogue.py`**, as `catalogue_bindings.py` is:
`test_api_public_response_hygiene.py` derives its endpoint list from the public route table.

**A designation is addressed by `term` in the request body**, never by a path segment (a
term can contain `/`) or an internal id (NFR-04/NFR-26).
`nptc.catalogue.designations.load_active_designation` resolves it by comparison key.

**`/amendment` writes to two storage homes.** ADR-0022 keeps the entry's own preferred
term on `catalogue_entry.preferred_term`, never on a `designation` row. The route looks for an
active designation first and falls back to the preferred term. That order is load-bearing:
nothing forbids an active synonym whose `term_key` equals the entry's `preferred_term_key`, and
trying the preferred term first would make that synonym uneditable. The request's `target`
names the home when `term` alone is ambiguous: `preferred_term` skips the designation lookup,
which is the only way to reach a preferred term that a synonym shadows, and `synonym` never
falls back to the entry.

**Every write requires `expected_row_version` (FR-38).** `designation` has no version column,
so the designation writes lock on `catalogue_entry.row_version` through
`nptc.catalogue.entries.entry_child_write`. The preferred-term branch of `/amendment` writes
`catalogue_entry` directly and locks through `save_entry`. The field is required, not
optional: bumping the version on a write that omitted it would invalidate every other
editor's still-current token.

**Warning-severity collisions come back on the write response**, not from a `GET`.
`warning_collisions` never raises, and a separate `GET` under `/catalogue` would be found by
the hygiene test's scanner and called without a credential.

**Acknowledging a collision needs a different permission.** `Permission.VALIDATION_ACKNOWLEDGE`
is held by `Role.REVIEWER` and `Role.ADMINISTRATOR`. It is not in `MFA_REQUIRED_PERMISSIONS`,
so its 403 carries no step-up challenge.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import ApiSettingsDep, AuditContextDep, get_session, permission_dep
from nptc.api.errors import DesignationCollisionResponse, VersionConflictResponse
from nptc.api.labels import AU_PREFERRED_TERM_PROVENANCE, SYNONYM_PROVENANCE, LabelProvenance
from nptc.api.prefix import API_PREFIX
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import BusinessKeyPath, Designation, designation_from_row
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal
from nptc.catalogue import queries
from nptc.catalogue.collisions import Collision, acknowledge_collision, warning_collisions
from nptc.catalogue.designations import (
    DesignationNotFoundError,
    DesignationNotRetiredError,
    add_synonyms,
    amend_designation,
    find_active_designation,
    load_active_designation,
    load_retired_designation,
)
from nptc.catalogue.designations import reinstate_designation as _reinstate_designation
from nptc.catalogue.designations import retire_designation as _retire_designation
from nptc.catalogue.entries import (
    EntryChanges,
    entry_child_write,
    load_entry_for_update,
    save_entry,
)
from nptc.catalogue.term_hygiene import clean_term, exceeds_maximum_length
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.designation import DesignationStatus
from nptc.settings import ApiSettings
from nptc_shared.similarity import collision_key

#: Each term holds a `pg_advisory_xact_lock` until commit and costs a
#: collision-check flush (`add_synonyms`), so the cap bounds both for one
#: request. FR-04's pasted-cell case is tens of terms.
_MAX_TERMS_PER_BATCH: Final[int] = 100

router = APIRouter(prefix="/catalogue", tags=["catalogue-admin"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403_EDIT: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `catalogue.edit_published`, "
        "or holds it but has not completed the MFA step-up this permission requires "
        "(the response then also carries a `WWW-Authenticate` step-up challenge)."
    ),
}
_RESPONSE_403_ACK: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The caller is authenticated but does not hold `validation.acknowledge`.",
}
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "No catalogue entry matches the given identifier, or no matching "
        "designation does - add/amend/retire look for an active one, "
        "reinstatement for a retired one."
    ),
}
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The request is well-formed but conflicts with the current state of the "
        "system - an error-severity collision against another entry (FR-05), a "
        "duplicate active term on this same entry, a designation already "
        "retired (or, for reinstatement, "
        "already active), or a concurrent acknowledgement of the same "
        "collision. Add, amend and retire address a designation by its "
        "currently-*active* term, so a retired one is simply not addressable that "
        "way any more (404, not 409); reinstatement addresses one by its "
        "currently-*retired* term instead, so a term that was never retired is its "
        "own 404 there."
    ),
}
#: Two 422 body shapes occur: a typed domain error (`ErrorResponse`), or a
#: pydantic failure that never reaches the route body (`HTTPValidationError`),
#: such as a bad `target`. Declaring only
#: `"model": ErrorResponse` would suppress the second.
_RESPONSE_422: Final[dict[str, Any]] = {
    "description": (
        "A field failed validation - an unrecognised `target`, a term that is empty "
        "after whitespace cleaning, or a changelog note that does not meet FR-37. Two "
        "distinct body shapes occur here: a typed domain error "
        "(`ErrorResponse`) or a pydantic validation failure (FastAPI's own "
        "`HTTPValidationError`)."
    ),
    "content": {
        "application/json": {
            "schema": {
                "anyOf": [
                    {"$ref": "#/components/schemas/ErrorResponse"},
                    {"$ref": "#/components/schemas/HTTPValidationError"},
                ]
            }
        }
    },
}

#: Two 409s carry a payload: FR-05 names the colliding entry (PRD SS17.2 item
#: 5) and FR-38 names the conflicting values. Declaring only
#: `"model": ErrorResponse` would type them as `{detail}` and the generated
#: client would drop the payload.
#:
#: `model` takes a union so both members register in `components/schemas` and
#: the document gets an `anyOf`; a hand-written `content` block with `$ref`s
#: would name schemas nothing else registers. The models live in
#: `nptc.api.errors` beside the handlers that build them, so the declared shape
#: and the emitted body cannot drift.
#:
#: Scoped per route, not folded into `_RESPONSE_409`: only add and amend run
#: `assert_no_error_collisions`, and only `/amendment` can write
#: `catalogue_entry`. A documented body a route cannot emit is a branch the
#: client can never exercise.
_RESPONSE_409_COLLISION: Final[dict[str, Any]] = {
    **_RESPONSE_409,
    "model": ErrorResponse | DesignationCollisionResponse,
    "description": (
        f"{_RESPONSE_409['description']} An error-severity collision carries "
        "`collisions[]` alongside `detail`, naming each colliding entry (FR-05)."
    ),
}
#: Shared by the two variants below so their descriptions cannot drift apart.
_STALE_VERSION_DESCRIPTION: Final[str] = (
    "A stale `expected_row_version` (FR-38) carries `business_key`, "
    "`expected_row_version`, `current_row_version`, `conflicts[]` (each with "
    "`field`, `submitted` and `current`) and `changed_by`/`changed_at`, so the "
    "caller can reconcile rather than retry blind."
)
#: Every write route requires `expected_row_version` (FR-38), so each can refuse
#: with a stale-version 409. Acknowledgement takes no lock token and uses plain
#: `_RESPONSE_409`.
_RESPONSE_409_VERSION: Final[dict[str, Any]] = {
    **_RESPONSE_409,
    "model": ErrorResponse | VersionConflictResponse,
    "description": f"{_RESPONSE_409['description']} {_STALE_VERSION_DESCRIPTION}",
}
_RESPONSE_409_COLLISION_AND_VERSION: Final[dict[str, Any]] = {
    **_RESPONSE_409_COLLISION,
    "model": ErrorResponse | DesignationCollisionResponse | VersionConflictResponse,
    "description": f"{_RESPONSE_409_COLLISION['description']} {_STALE_VERSION_DESCRIPTION}",
}

#: Shared by add, amend, retire and reinstate: the same permission and the same
#: statuses.
_RESPONSES_WRITE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_EDIT,
    404: _RESPONSE_404,
    409: _RESPONSE_409_VERSION,
    422: _RESPONSE_422,
}
#: Add: can collide (FR-05) as well as version-conflict.
_RESPONSES_ADD: Final[dict[int | str, dict[str, Any]]] = {
    **_RESPONSES_WRITE,
    409: _RESPONSE_409_COLLISION_AND_VERSION,
}
#: Amend: can do both, and is the only route here that writes an entry
#: directly on its preferred-term branch.
_RESPONSES_AMEND: Final[dict[int | str, dict[str, Any]]] = {
    **_RESPONSES_WRITE,
    409: _RESPONSE_409_COLLISION_AND_VERSION,
}
#: Reinstate can collide (FR-05) as amend can: reactivating a row meets the same
#: partial unique indexes a fresh insert does.
_RESPONSES_REINSTATE: Final[dict[int | str, dict[str, Any]]] = _RESPONSES_AMEND
_RESPONSES_ACKNOWLEDGE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_ACK,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}


class CollisionWarning(BaseModel):
    """One warning-severity collision (FR-05): the same term active on
    another live entry. Names that entry's public identifier and preferred
    term, never its internal id (NFR-04/NFR-26) - the same shape the 409
    handler for the *error*-severity case already returns.

    `label_provenance["term"]` is always `SYNONYM_PROVENANCE` (FR-98),
    never `AU_PREFERRED_TERM`: both call sites'
    own comments (`add_designations_route`/`amend_designation_route`) prove
    the preferred branch never produces a `CollisionWarning` at all -
    `warning_collisions` only ever looks for another live entry's active
    *synonym*, so a non-empty `warnings` list is only ever reachable from
    the synonym branch. `label_provenance["preferred_term"]` is the
    *colliding* entry's own catalogue preferred term, matching
    `EntrySummary.preferred_term`'s own designation type.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["collision"]
    term: str
    business_key: str
    preferred_term: str
    label_provenance: dict[str, LabelProvenance]


#: `CollisionWarning.label_provenance` is the same fixed dict on every
#: instance - see the model's own docstring for why `term` is always a
#: synonym here.
_COLLISION_WARNING_LABEL_PROVENANCE: dict[str, LabelProvenance] = {
    "term": SYNONYM_PROVENANCE,
    "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
}


def _collision_warning(collision: Collision) -> CollisionWarning:
    return CollisionWarning(
        kind="collision",
        term=collision.term,
        business_key=collision.business_key,
        preferred_term=collision.preferred_term,
        label_provenance=_COLLISION_WARNING_LABEL_PROVENANCE,
    )


class LengthWarning(BaseModel):
    """FR-86: the catalogue's own preferred term exceeds the configured
    maximum length. Non-blocking, the same "warn, never raise" shape as
    `CollisionWarning` - a hard block would make an existing over-length
    entry uneditable, the specific failure FR-86 exists to prevent.

    A separate union member from `CollisionWarning`, not a widened
    `CollisionWarning`: its shape (a colliding entry's business key, term,
    provenance) has no field this could honestly populate, and this one has
    no colliding entry to name. Both ride the one `warnings` list so a
    further warning class adds a member, not a response field.

    Only ever produced on `amend_designation_route`'s preferred-term
    branch: FR-85's `length` is defined against the catalogue's own
    preferred term (`nptc.catalogue.term_hygiene.preferred_term_length`),
    which lives on `catalogue_entry.preferred_term`, never on a
    `designation` row (ADR-0022) - there is no other branch this could ever
    apply to.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["length"]
    length: int
    max_length: int


#: Every write response's `warnings` item. `kind` is the discriminator, so the
#: OpenAPI document and the generated client narrow on it (ADR-0045).
DesignationWarning = Annotated[CollisionWarning | LengthWarning, Field(discriminator="kind")]


def _length_warning(entry: CatalogueEntry, settings: ApiSettings) -> LengthWarning | None:
    """`None` when no maximum is configured (FR-86) or the length does not exceed
    it. `entry.length` is read after `save_entry` has written the cleaned term, so
    this compares the value FR-85 publishes."""
    maximum = settings.max_preferred_term_length
    if maximum is None:
        return None
    length = entry.length
    if not exceeds_maximum_length(length, maximum):
        return None
    return LengthWarning(kind="length", length=length, max_length=maximum)


class AddDesignationsRequest(BaseModel):
    """The body of `POST /catalogue/entries/{business_key}/designations`.

    `terms` is a batch - the common case is pasting a delimiter-corrupted synonym cell
    (FR-04). Capped at `_MAX_TERMS_PER_BATCH`: each term holds a `pg_advisory_xact_lock` until
    commit and costs its own collision-check flush (`add_synonyms`'s own docstring), so an
    unbounded batch is an unbounded amount of lock contention for one request.
    """

    model_config = ConfigDict(frozen=True)

    terms: list[str] = Field(min_length=1, max_length=_MAX_TERMS_PER_BATCH)
    reason: str
    #: FR-38: the entry's `row_version` as the caller last read it. The whole
    #: batch bumps it once (`entry_child_write`), and a concurrent editor of
    #: another term on this entry is refused.
    expected_row_version: int = Field(ge=1)


class DesignationWriteResult(BaseModel):
    """`add_designations`'s response: the created row(s), any warning-severity
    collisions, and the entry's new `row_version` (FR-38) - so a client
    never has to re-fetch the entry just to learn its next lock token."""

    model_config = ConfigDict(frozen=True)

    designations: list[Designation]
    warnings: list[DesignationWarning]
    row_version: int


class AmendTarget(StrEnum):
    """Which storage home an amendment's `term` means (see the module docstring)."""

    PREFERRED_TERM = "preferred_term"
    SYNONYM = "synonym"


class AmendDesignationRequest(BaseModel):
    """The body of `POST .../designations/amendment`. `term` addresses the
    designation to edit; `new_term` is what it becomes. Editing in place
    (rather than retire-and-re-add) is `nptc.catalogue.designations.
    amend_designation`'s own choice - see that function's docstring.

    `term` also addresses the entry's *own* preferred term, which is
    not a designation row at all (ADR-0022) - see the module docstring for
    the dispatch and `expected_row_version` for the lock it requires."""

    model_config = ConfigDict(frozen=True)

    term: str
    new_term: str = Field(min_length=1)
    reason: str
    #: Which storage home `term` means, when it could mean either. Unset: an
    #: active `designation` row first, then the entry's own preferred term (see
    #: the module docstring). `preferred_term` addresses
    #: `catalogue_entry.preferred_term` directly. `synonym` addresses a
    #: `designation` row and never falls back to the entry.
    target: AmendTarget | None = None
    #: FR-38: required on both branches, so both refuse a stale version alike.
    expected_row_version: int = Field(ge=1)


class AmendDesignationResult(BaseModel):
    """The amended term, plus the entry's version after the write.

    `designation` is the same shape on both branches, including the one
    that did not touch a `designation` row at all: the catalogue's own
    preferred term comes back rendered as an active designation, and
    `label_provenance.designation` (`au_preferred_term` or `synonym`) says
    which home it came from. ADR-0022's split between two storage homes is
    not something a client should have to model (see the module docstring).

    `row_version` is the entry's, on both branches, and is what a client
    sends back as `expected_row_version` on its next write - so a save
    never has to be followed by a re-fetch just to learn the new token. It
    advances on both branches (FR-38): a `designation` row has no version
    of its own, but amending one bumps the entry's counter via
    `nptc.catalogue.entries.entry_child_write`, the same way the
    preferred-term branch's `save_entry` always has.

    `warnings` carries a `LengthWarning` (FR-86) only on the preferred-term
    branch, and only when a maximum is configured and exceeded. A synonym
    amend can carry only collision warnings.
    """

    model_config = ConfigDict(frozen=True)

    designation: Designation
    warnings: list[DesignationWarning]
    row_version: int


class RetireDesignationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    term: str
    reason: str
    #: FR-38: required; see the module docstring.
    expected_row_version: int = Field(ge=1)


class RetireDesignationResult(BaseModel):
    """`retire_designation_route`'s response: the retired row, plus the
    entry's new `row_version` (FR-38).

    Declared here rather than returning a bare `Designation`, which has
    nowhere to carry `row_version` - `Designation` is the shared public read
    model (`catalogue_shared.py`) and must not grow an admin-only field,
    matching `catalogue_bindings.py`'s own `BindingWriteResult` precedent."""

    model_config = ConfigDict(frozen=True)

    designation: Designation
    row_version: int


class ReinstateDesignationRequest(BaseModel):
    """The body of `POST .../designations/reinstatement`.
    `term` addresses the designation to reinstate - resolved against the
    most-recently-retired row matching `(entry, term)`
    (`nptc.catalogue.designations.load_retired_designation`), never a
    specific row's internal id, matching every other route in this module."""

    model_config = ConfigDict(frozen=True)

    term: str
    reason: str
    #: FR-38: required; see the module docstring.
    expected_row_version: int = Field(ge=1)


class ReinstateDesignationResult(BaseModel):
    """`reinstate_designation_route`'s response: the reinstated row, any
    warning-severity collisions, and the entry's new `row_version` (FR-38).

    A new model, not a reuse of `DesignationWriteResult`: this route always
    acts on exactly one row, and `DesignationWriteResult.designations`
    being a list would misdescribe that. Shaped like
    `AmendDesignationResult` instead, which reinstatement otherwise matches
    exactly: one designation, warnings, row_version."""

    model_config = ConfigDict(frozen=True)

    designation: Designation
    warnings: list[DesignationWarning]
    row_version: int


class AcknowledgeCollisionRequest(BaseModel):
    """The body of `POST .../designations/acknowledgement`. `term` is the
    surface form the caller is acknowledging a warning for - resolved to a
    comparison key here (`nptc.catalogue.designations.clean_term` then
    `nptc_shared.similarity.collision_key`), matching what
    `warning_collisions` itself keys on, so acknowledging any surface form
    that folds to the same key silences the same warning."""

    model_config = ConfigDict(frozen=True)

    term: str
    reason: str


class CollisionAcknowledgementResponse(BaseModel):
    """Confirms an acknowledgement was recorded. No internal id, no
    `entry_id`, and deliberately no `acknowledged_by_user_id` either
    (NFR-04/NFR-26) - which administrator or reviewer acknowledged a
    collision is an audit-log fact, not a public response field.

    `created` distinguishes "this call recorded it" (`True`) from "this
    exact `(entry, term)` was already acknowledged, by an earlier
    call" (`False`) - `acknowledge_collision` is idempotent (see its own
    docstring), and without this flag a caller cannot tell those two cases
    apart, nor notice that `reason` below is the *original* note rather
    than the one this call just submitted."""

    model_config = ConfigDict(frozen=True)

    reason: str
    created: bool


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))
#: A value dependency, not `dependencies=[...]`: `acknowledge_collision` needs the
#: resolved `Principal`, and `permission_dep` returns it after the check, so one
#: dependency does both.
AcknowledgerDep = Annotated[Principal, Depends(permission_dep(Permission.VALIDATION_ACKNOWLEDGE))]


@router.post(
    "/entries/{business_key}/designations",
    summary="Add one or more synonyms to a catalogue entry",
    status_code=201,
    responses=_RESPONSES_ADD,
    dependencies=[_EDIT],
)
def add_designations(
    session: SessionDep,
    ctx: AuditContextDep,
    response: Response,
    business_key: BusinessKeyPath,
    body: Annotated[AddDesignationsRequest, Body()],
) -> DesignationWriteResult:
    entry = load_entry_for_update(session, business_key)
    # One `entry_child_write` around the whole batch: a failure part-way rolls the
    # batch back, and `catalogue_entry.row_version` bumps once, not once per term
    # (FR-38; `test_a_batch_add_bumps_row_version_once_not_once_per_term`).
    #
    # Lock order is append lock, then collision-key `pg_advisory_xact_lock`
    # (`assert_no_error_collisions`); see ADR-0035, "Lock ordering".
    # `entry_child_write` takes the append lock first. `add_designation` takes it
    # again as its own first statement, a re-entrant no-op that keeps the order
    # right for a caller that skips `entry_child_write`.
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        created = add_synonyms(session, ctx, entry=entry, terms=body.terms, reason=body.reason)
    response.headers["Location"] = f"{API_PREFIX}{router.prefix}/entries/{business_key}"
    created_ids = {designation.id for designation in created}
    rows = [
        row
        for row in queries.load_designations_any_status(session, (entry.id,))
        if row.id in created_ids
    ]
    warnings = warning_collisions(
        session, entry=entry, terms=[designation.term for designation in created]
    )
    return DesignationWriteResult(
        designations=[designation_from_row(row) for row in rows],
        warnings=[_collision_warning(warning) for warning in warnings],
        row_version=entry.row_version,
    )


def _targets_preferred_term(entry: CatalogueEntry, body: AmendDesignationRequest) -> bool:
    """Whether this request means the entry's own preferred term rather than a
    `designation` row (ADR-0022).

    `term` must name the preferred term even with `target="preferred_term"`. `target`
    only chooses which storage home to look in. On this route `term` is the address,
    not the new value, so ignoring it would let a mistyped `term` rename the preferred
    term instead of returning 404. The requirement costs the escape hatch nothing: a
    synonym that shadows the preferred term folds to the same `collision_key`, so a
    caller reaching past it names a matching term anyway.

    The comparison uses the stored `preferred_term_key`, not a key recomputed from
    `entry.preferred_term`: `CatalogueEntry`'s `@validates("preferred_term")` hook
    writes it with the same `collision_key(clean_term(...))` composition, and
    `nptc.catalogue.collisions` compares stored keys and never recomputes them. A case
    or punctuation variant of the term therefore resolves, as it does for a designation.
    """
    if body.target is AmendTarget.SYNONYM:
        return False
    return entry.preferred_term_key == collision_key(clean_term(body.term))


def _preferred_term_as_designation(entry: CatalogueEntry) -> Designation:
    """The catalogue's own preferred term in the shape this API gives every term.
    ADR-0022 guarantees no `designation` row exists for it, so the constant `status`
    restates that invariant rather than a stored row.

    `label_provenance` is `AU_PREFERRED_TERM_PROVENANCE` (FR-98), not the
    `SYNONYM_PROVENANCE` that `designation_from_row` gives: it is how a client tells
    the entry's own preferred term from a synonym.
    """
    return Designation(
        term=entry.preferred_term,
        status=str(DesignationStatus.ACTIVE),
        length=entry.length,
        label_provenance=AU_PREFERRED_TERM_PROVENANCE,
    )


@router.post(
    "/entries/{business_key}/designations/amendment",
    summary="Edit an entry's active designation, or its own preferred term, in place",
    responses=_RESPONSES_AMEND,
    dependencies=[_EDIT],
)
def amend_designation_route(
    session: SessionDep,
    ctx: AuditContextDep,
    settings: ApiSettingsDep,
    business_key: BusinessKeyPath,
    body: Annotated[AmendDesignationRequest, Body()],
) -> AmendDesignationResult:
    """One route, two storage homes.

    `target="preferred_term"` addresses the entry's own preferred term outright.
    Otherwise `term` resolves against an active `designation` row first, falling
    back to the preferred term only where there is no such row - see the module
    docstring for why that fallback order is designation-first, and why the explicit
    `target` exists at all.
    """
    entry = load_entry_for_update(session, business_key)
    designation = (
        None
        if body.target is AmendTarget.PREFERRED_TERM
        # No designation row holds the preferred term (ADR-0022), so the lookup is
        # skipped rather than run and discarded.
        else find_active_designation(session, entry_id=entry.id, term=body.term)
    )

    if designation is None and _targets_preferred_term(entry, body):
        save_entry(
            session,
            ctx,
            business_key=business_key,
            expected_row_version=body.expected_row_version,
            changes=EntryChanges(preferred_term=body.new_term),
            reason=body.reason,
            max_preferred_term_length=settings.max_preferred_term_length,
        )
        session.flush()
        # No collision warnings: a preferred term matching another entry's synonym is
        # an *error*-severity collision `save_entry` has already raised.
        length_warning = _length_warning(entry, settings)
        return AmendDesignationResult(
            designation=_preferred_term_as_designation(entry),
            warnings=[] if length_warning is None else [length_warning],
            row_version=entry.row_version,
        )

    # The 404 for an unresolvable term sits inside the lock, so
    # `entry_child_write`'s version check runs first and a stale caller sees the
    # conflict, not a 404 (as in `catalogue_bindings.py`'s `retire_binding`).
    # Lock order is the one `add_designations` documents.
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        if designation is None:
            # Raised here, not via `load_active_designation`, which would re-run
            # the same `SELECT` only to fail. The message is for the log only (the
            # handler never echoes `str(exc)`), so it says what this route checked.
            raise DesignationNotFoundError(
                f"entry {entry.id} has no active designation for term {body.term!r}, "
                "and it is not the entry's own preferred term"
            )
        amended = amend_designation(
            session,
            ctx,
            entry=entry,
            designation=designation,
            new_term=body.new_term,
            reason=body.reason,
        )
    amended_id = amended.id
    row = queries.load_designation_by_id(session, amended_id)
    if row is None:
        raise RuntimeError(f"designation {amended_id} not found immediately after being amended")
    warnings = warning_collisions(session, entry=entry, terms=[amended.term])
    return AmendDesignationResult(
        designation=designation_from_row(row),
        warnings=[_collision_warning(warning) for warning in warnings],
        # The entry's, bumped by `entry_child_write`: a `designation` row has no
        # version of its own (FR-38).
        row_version=entry.row_version,
    )


@router.post(
    "/entries/{business_key}/designations/retirement",
    summary="Retire an entry's active designation",
    responses=_RESPONSES_WRITE,
    dependencies=[_EDIT],
)
def retire_designation_route(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    body: Annotated[RetireDesignationRequest, Body()],
) -> RetireDesignationResult:
    entry = load_entry_for_update(session, business_key)
    # `load_active_designation` (a 404 for a missing or retired term) runs inside
    # the lock, after the version check, so a stale caller sees the conflict, not
    # a 404 (as in `catalogue_bindings.py`'s `retire_binding`).
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        designation = load_active_designation(session, entry_id=entry.id, term=body.term)
        _retire_designation(session, ctx, designation=designation, reason=body.reason)
    designation_id = designation.id
    row = queries.load_designation_by_id(session, designation_id)
    if row is None:
        raise RuntimeError(f"designation {designation_id} not found immediately after retirement")
    return RetireDesignationResult(
        designation=designation_from_row(row), row_version=entry.row_version
    )


@router.post(
    "/entries/{business_key}/designations/reinstatement",
    summary="Reinstate an entry's most-recently-retired designation",
    responses=_RESPONSES_REINSTATE,
    dependencies=[_EDIT],
)
def reinstate_designation_route(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    body: Annotated[ReinstateDesignationRequest, Body()],
) -> ReinstateDesignationResult:
    """Reinstate a retired designation. Both lookups run inside the lock,
    after `entry_child_write`'s own version check, matching every other
    route here (FR-38).

    The already-active check runs first, and outside `load_retired_
    designation` itself: a term that already has an active designation
    (the exact scenario re-adding a retired term creates today) must
    refuse with a 409 naming that conflict, not the 404 a term that was
    simply never retired gets - `load_retired_designation` only ever
    inspects retired rows, so it cannot tell the two cases apart on its
    own (see its own docstring)."""
    entry = load_entry_for_update(session, business_key)
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        active = find_active_designation(session, entry_id=entry.id, term=body.term)
        if active is not None:
            raise DesignationNotRetiredError(
                f"entry {entry.id} already has an active designation for term {body.term!r}"
            )
        designation = load_retired_designation(session, entry_id=entry.id, term=body.term)
        reinstated = _reinstate_designation(
            session, ctx, entry=entry, designation=designation, reason=body.reason
        )
    reinstated_id = reinstated.id
    row = queries.load_designation_by_id(session, reinstated_id)
    if row is None:
        raise RuntimeError(f"designation {reinstated_id} not found immediately after reinstatement")
    warnings = warning_collisions(session, entry=entry, terms=[reinstated.term])
    return ReinstateDesignationResult(
        designation=designation_from_row(row),
        warnings=[_collision_warning(warning) for warning in warnings],
        row_version=entry.row_version,
    )


@router.post(
    "/entries/{business_key}/designations/acknowledgement",
    summary="Acknowledge a warning-severity collision on this entry",
    responses=_RESPONSES_ACKNOWLEDGE,
)
def acknowledge_designation_collision(
    session: SessionDep,
    ctx: AuditContextDep,
    acknowledger: AcknowledgerDep,
    business_key: BusinessKeyPath,
    body: Annotated[AcknowledgeCollisionRequest, Body()],
) -> CollisionAcknowledgementResponse:
    entry = load_entry_for_update(session, business_key)
    term_key = collision_key(clean_term(body.term))
    acknowledgement, created = acknowledge_collision(
        session,
        ctx,
        acknowledger=acknowledger,
        entry=entry,
        term_key=term_key,
        reason=body.reason,
    )
    session.flush()
    return CollisionAcknowledgementResponse(reason=acknowledgement.reason, created=created)
