"""Write routes over `designation` (issue #224, FR-04, FR-05, FR-36, FR-37, NFR-08).

Follows `routers/catalogue_bindings.py`'s house style, plus `registry.py`'s
two later refinements: response models declared here, `ConfigDict(frozen=True)`,
the return-type annotation drives the response model (never `response_model=`),
a `Final` `_RESPONSES_*` dict per route naming only the statuses that route can
actually produce, and no try/except in a route body - every domain exception
carries `http_status` and is mapped centrally by `nptc.api.errors`. Every 422
below documents both body shapes (`ErrorResponse` and FastAPI's own
`HTTPValidationError`), not just the first - a route with a request body can
produce either, and declaring only `ErrorResponse` silently suppresses the
second (issue #223 review finding 2).

**A separate router from `catalogue.py`, on purpose** - same reasoning as
`catalogue_bindings.py`'s own module docstring: that module is the public
read surface and `test_api_public_response_hygiene.py` derives its endpoint
list from its route table.

**Addressing a designation: by term in the request body, never a path
segment or an internal id.** The public `Designation` model carries no `id`
(NFR-04/NFR-26, matching `Binding`'s own rule), and a term can contain `/`
(`"CD4/CD8 ratio"`) - FastAPI decodes a path segment before routing, so a
term with a slash would either 404 against the wrong route or need
double-encoding no client should have to reason about. Every write below
therefore takes its target term in the body, resolved to the exact active
row via `nptc.catalogue.designations.load_active_designation` (looked up by
comparison key, so a case/punctuation variant of the stored term still
resolves it).

**`/amendment` writes to two storage homes, and dispatches between them
(issue #227).** ADR-0022 keeps the catalogue's own en-AU preferred term on
`catalogue_entry.preferred_term`, never a `designation` row
(`ck_designation_no_en_au_preferred`). Rather than expose that split as a
second endpoint, `/amendment` resolves `term` against both: an active
`designation` row if there is one, otherwise the entry's own preferred term.
Every term the catalogue holds is a designation as far as this API is
concerned - one route, one mental model, two storage homes - and the
preferred-term branch even returns its result shaped as a `Designation`
(`use="preferred"`, `language="en-AU"`).

**Designation-first, and that order is load-bearing.** Nothing forbids an
entry from carrying an active en-AU synonym whose `term_key` equals its own
`preferred_term_key`: `ix_designation_no_duplicate_active_term` is
designation-vs-designation only, and `assert_no_error_collisions` compares
against *other* live entries. Resolving the preferred term first would
therefore make an existing synonym unreachable for editing, silently
changing what a shipped route does. Taking the designation first means the
new branch only ever claims what this route already 404s on today.

**FR-38 (issue #300): every write here requires `expected_row_version`.**
`designation` has no version column of its own (ADR-0022's other storage
home besides `catalogue_entry.preferred_term`), so all three routes -
`add_designations`, `amend_designation_route`'s designation branch, and
`retire_designation_route` - share `catalogue_entry.row_version` as their
lock, via `nptc.catalogue.entries.entry_child_write` - the same argument
`catalogue_bindings.py` already makes for `code_binding` (issue #60).
`amend_designation_route`'s preferred-term branch writes `catalogue_entry`
directly and keeps using `save_entry`, which already required and bumped
the version before this issue; only the designation branch's lock was ever
missing. Required outright rather than left optional, unlike issue #227's
first cut here: bumping the version on a write that let the field stay
optional would silently invalidate every other editor's still-current token
the moment a caller that omits it saves, which is worse than the gap it
would close. This closes the concurrency gap `docs/architecture/
catalogue-write-api.md` used to describe as opt-in: two administrators
editing different terms on one entry, by any combination of add, amend and
retire, now conflict.

**Warning-severity collisions ride back on a write response, never a
separate `GET`.** `nptc.catalogue.collisions.warning_collisions` never
raises (a warning permits the save by construction, see its own docstring) -
`add_designations`/`amend_designation` below call it after their own write
and return what it finds alongside the row(s) just written, rather than
exposing it as its own endpoint under `/catalogue` that
`test_api_public_response_hygiene.py`'s GET scanner would otherwise pick up
and attempt to exercise without a credential.

**Acknowledging a collision is gated on a different permission from the
other three routes.** `Permission.VALIDATION_ACKNOWLEDGE` is held by
`Role.REVIEWER` *and* `Role.ADMINISTRATOR` - unlike `catalogue.
edit_published` (Administrator-only), it is not in `MFA_REQUIRED_PERMISSIONS`
and its 403 carries no step-up challenge.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import AuditContextDep, get_session, permission_dep
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
    add_designation,
    add_synonyms,
    amend_designation,
    find_active_designation,
    load_active_designation,
)
from nptc.catalogue.designations import retire_designation as _retire_designation
from nptc.catalogue.entries import (
    EntryChanges,
    entry_child_write,
    load_entry_for_update,
    save_entry,
)
from nptc.catalogue.term_hygiene import clean_term, validate_language_tag
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.designation import DesignationStatus, DesignationUse
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

#: A batch this large is no longer the pasted-cell case FR-04 describes
#: (realistically tens of terms) - `add_synonyms`' own docstring notes each
#: term holds a `pg_advisory_xact_lock` until commit, so an unbounded batch
#: lets one authenticated caller hold an unbounded number of locks, plus a
#: collision-check flush per term, in one request (issue #224 review
#: finding 4).
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
    "description": "No catalogue entry, or no active designation, matches the given identifier.",
}
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The request is well-formed but conflicts with the current state of the "
        "system - an error-severity collision against another entry (FR-05), a "
        "duplicate active term or a second active preferred term in one language "
        "on this same entry, a designation already retired, or a concurrent "
        "acknowledgement of the same collision. A term already retired, or never "
        "added, is a 404 here rather than a 409: every route below addresses a "
        "designation by its currently-*active* term, so a retired one is simply "
        "not addressable this way any more, not a conflicting state."
    ),
}
#: Two distinct 422 body shapes occur on every route below: a typed domain
#: error (`ErrorResponse`) - a term left empty after whitespace cleaning, a
#: malformed BCP-47 language tag - or a pydantic validation failure that
#: never reaches the route body at all (FastAPI's own `HTTPValidationError`),
#: which is what a bad `use` value or this module's own cross-field checks
#: (the en-AU-preferred exclusion, an empty batch) produce. Declaring only
#: `"model": ErrorResponse` would silently suppress the second (registry.py
#: precedent, issue #223 review finding 2).
_RESPONSE_422: Final[dict[str, Any]] = {
    "description": (
        "A field failed validation - an unrecognised `use`, a malformed language tag, "
        "a term that is empty after whitespace cleaning, or a changelog note that does "
        "not meet FR-37. Two distinct body shapes occur here: a typed domain error "
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

#: Not every 409 below is an `ErrorResponse`. Two carry a payload, because a
#: bare sentence would withhold exactly what the requirement exists to give
#: the caller: FR-05 names the colliding entry (PRD SS17.2 item 5), and FR-38
#: names the conflicting values so the caller can reconcile rather than retry
#: blind. Declaring only `"model": ErrorResponse` types those branches as
#: `{detail}` for #147's generated client, which then drops the payload
#: entirely - the same defect `_RESPONSE_422` above already fixed for its own
#: second body shape (issue #223 review finding 2, and issue #227 review).
#:
#: `model` takes a union so *both* members are registered in
#: `components/schemas` and the document gets an `anyOf`; a hand-written
#: `content` block with `$ref`s would name schemas nothing else registers.
#: The models live in `nptc.api.errors` next to the handlers that build
#: them, so the declared shape and the emitted body cannot drift.
#:
#: Scoped per route rather than folded into `_RESPONSE_409`: only
#: `add_designations` and `amend_designation_route` call a service function
#: that runs `assert_no_error_collisions`, and only `/amendment` can write
#: `catalogue_entry`. Retirement and acknowledgement can produce neither, and
#: a documented body a route cannot emit is a branch #147's client can never
#: exercise - this module's own rule.
_RESPONSE_409_COLLISION: Final[dict[str, Any]] = {
    **_RESPONSE_409,
    "model": ErrorResponse | DesignationCollisionResponse,
    "description": (
        f"{_RESPONSE_409['description']} An error-severity collision carries "
        "`collisions[]` alongside `detail`, naming each colliding entry (FR-05)."
    ),
}
#: Shared verbatim by `_RESPONSE_409_VERSION` and
#: `_RESPONSE_409_COLLISION_AND_VERSION` below - a `Final[str]` rather than
#: two copies of the same sentence, so the two OpenAPI descriptions cannot
#: drift apart (issue #300 review, minor).
_STALE_VERSION_DESCRIPTION: Final[str] = (
    "A stale `expected_row_version` (FR-38) carries `business_key`, "
    "`expected_row_version`, `current_row_version`, `conflicts[]` (each with "
    "`field`, `submitted` and `current`) and `changed_by`/`changed_at`, so the "
    "caller can reconcile rather than retry blind."
)
#: Every write route below now requires `expected_row_version` (FR-38, issue
#: #300), so every one of them can also refuse with a stale-version 409 -
#: `_RESPONSE_409` (acknowledgement only, which takes no lock token) is the
#: one 409 here that cannot.
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

#: Shared by add/amend/retire: all three are gated on the same permission
#: and can fail with the same set of statuses. One constant, not three
#: identical dicts, so a future divergence between them is a deliberate
#: edit rather than an accident of copy-paste (issue #224 review, minor).
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

    `label_provenance["term"]` is always `SYNONYM_PROVENANCE` (FR-98, issue
    #144), never `PREFERRED_VARIANT`/`AU_PREFERRED_TERM`: both call sites'
    own comments (`add_designations_route`/`amend_designation_route`) prove
    the preferred branch never produces a `CollisionWarning` at all -
    `warning_collisions` only ever looks for another live entry's active
    *synonym*, so a non-empty `warnings` list is only ever reachable from
    the synonym branch. `label_provenance["preferred_term"]` is the
    *colliding* entry's own catalogue preferred term, matching
    `EntrySummary.preferred_term`'s own designation type.
    """

    model_config = ConfigDict(frozen=True)

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
        term=collision.term,
        business_key=collision.business_key,
        preferred_term=collision.preferred_term,
        label_provenance=_COLLISION_WARNING_LABEL_PROVENANCE,
    )


class _WithLanguage(BaseModel):
    """Every request model below that carries a caller-supplied `language`
    inherits this rather than declaring the field itself, so all four get
    the exact same treatment: checked well-formed and folded to canonical
    BCP-47 casing (`nptc.catalogue.term_hygiene.validate_language_tag`)
    during request parsing, before any route body or service function ever
    sees the value.

    This closes two gaps a per-route fix would not (issue #224 review):
    `POST .../acknowledgement` inserts straight into
    `designation_collision_acknowledgement`, which - unlike `Designation` -
    has no `@validates("language")` hook of its own, so a malformed tag
    there previously reached the database's `CHECK` constraint as an
    unmapped `IntegrityError` (finding 1); and `en-au`/`en-AU` previously
    compared unequal in `AddDesignationsRequest._reject_en_au_preferred`
    below, in `assert_no_error_collisions`'s `language == DEFAULT_LANGUAGE`
    branching, and in the two designation partial unique indexes, letting a
    lowercase `en-au` preferred designation slip past the ADR-0022
    invariant those all exist to enforce (finding 2). A field validator
    runs during construction even though every model here is frozen -
    `frozen=True` only blocks *reassignment* after the model exists."""

    language: str = DEFAULT_LANGUAGE

    @field_validator("language")
    @classmethod
    def _canonicalise_language(cls, value: str) -> str:
        return validate_language_tag(value)


class AddDesignationsRequest(_WithLanguage):
    """The body of `POST /catalogue/entries/{business_key}/designations`.

    `use` is typed against `DesignationUse` (matching `CodeBindingEditionHint`'s
    own precedent in `catalogue_bindings.py`), so an unrecognised value is a
    pydantic 422 before any row is touched, rather than an unmapped
    `ck_designation_use` `IntegrityError`. `terms` is a batch - the common
    case is pasting a delimiter-corrupted synonym cell (FR-04) - but a
    preferred variant permits at most one, since at most one can ever be
    active per `(entry, language)`. Capped at `_MAX_TERMS_PER_BATCH`: each
    term holds a `pg_advisory_xact_lock` until commit and costs its own
    collision-check flush (`add_synonyms`'s own docstring), so an unbounded
    batch is an unbounded amount of lock contention for one request (issue
    #224 review finding 4).
    """

    model_config = ConfigDict(frozen=True)

    terms: list[str] = Field(min_length=1, max_length=_MAX_TERMS_PER_BATCH)
    use: DesignationUse = DesignationUse.SYNONYM
    reason: str
    #: FR-38 (issue #300): the entry's `row_version` as the caller last read
    #: it, so the whole batch bumps the entry's counter as one write
    #: (`nptc.catalogue.entries.entry_child_write`) and a concurrent editor
    #: of a different term on this entry is refused rather than silently
    #: unnoticed.
    expected_row_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _reject_en_au_preferred(self) -> AddDesignationsRequest:
        # ck_designation_no_en_au_preferred (ADR-0022): the catalogue's own
        # en-AU preferred term is `catalogue_entry.preferred_term`, never a
        # designation row - a CHECK constraint, not a unique index, so it
        # cannot be translated from an IntegrityError's constraint name the
        # way the two unique-index cases below are. Refused here instead.
        # `self.language` is already canonicalised by `_WithLanguage`, so
        # `en-au` is caught here too, not only `en-AU` (issue #224 review
        # finding 2).
        if self.use is DesignationUse.PREFERRED and self.language == "en-AU":
            raise ValueError(
                "the catalogue's own en-AU preferred term is not a designation - "
                "amend it through POST .../designations/amendment, naming it as "
                "`term` and supplying `expected_row_version` (ADR-0022)"
            )
        return self

    @model_validator(mode="after")
    def _reject_a_preferred_batch(self) -> AddDesignationsRequest:
        if self.use is DesignationUse.PREFERRED and len(self.terms) != 1:
            raise ValueError(
                "only one preferred term can be added at a time - "
                "at most one can ever be active per language"
            )
        return self


class DesignationWriteResult(BaseModel):
    """`add_designations`'s response: the created row(s), any warning-severity
    collisions, and the entry's new `row_version` (FR-38, issue #300) - so a
    client never has to re-fetch the entry just to learn its next lock
    token."""

    model_config = ConfigDict(frozen=True)

    designations: list[Designation]
    warnings: list[CollisionWarning]
    row_version: int


class AmendDesignationRequest(_WithLanguage):
    """The body of `POST .../designations/amendment`. `term` addresses the
    designation to edit; `new_term` is what it becomes. Editing in place
    (rather than retire-and-re-add) is `nptc.catalogue.designations.
    amend_designation`'s own choice - see that function's docstring.

    `term` also addresses the entry's *own* en-AU preferred term, which is
    not a designation row at all (ADR-0022) - see the module docstring for
    the dispatch and `expected_row_version` for the lock it requires."""

    model_config = ConfigDict(frozen=True)

    term: str
    new_term: str = Field(min_length=1)
    reason: str
    #: Which of the two storage homes `term` means, when it could mean
    #: either (issue #227 review). Left unset, the dispatch resolves an
    #: active `designation` row first and falls back to the entry's own
    #: preferred term - which is unambiguous until an entry holds a synonym
    #: whose comparison key equals its own preferred term. Nothing forbids
    #: that state and `POST .../designations` will create it, so without a
    #: disambiguator the preferred term would be unreachable for editing
    #: from then on, and a caller asking for it would silently move the
    #: synonym instead.
    #:
    #: `preferred` + `en-AU` therefore addresses `catalogue_entry.
    #: preferred_term` directly, never a designation - ADR-0022 guarantees
    #: there is no such row to confuse it with. `preferred` in any other
    #: language, and `synonym` in any language, address a `designation` row
    #: and never fall back to the entry.
    use: DesignationUse | None = None
    #: FR-38 (issue #300): required on both branches. The preferred-term
    #: branch always required it (`save_entry`); the designation branch now
    #: does too, so a caller who omits it can no longer silently bump the
    #: entry's version out from under another editor mid-write, and both
    #: branches refuse a stale version the same way (issue #227's original
    #: cut left this branch optional - see the module docstring for why that
    #: could not simply start bumping while still optional).
    expected_row_version: int = Field(ge=1)


class AmendDesignationResult(BaseModel):
    """The amended term, plus the entry's version after the write.

    `designation` is the same shape on both branches, including the one
    that did not touch a `designation` row at all: the catalogue's own
    en-AU preferred term comes back rendered as
    `use="preferred", language="en-AU"`. That is this API's whole premise -
    every term the catalogue holds is a designation, and ADR-0022's split
    between two storage homes is not something a client should have to
    model (issue #224's own module docstring).

    `row_version` is the entry's, on both branches, and is what a client
    sends back as `expected_row_version` on its next write - so a save
    never has to be followed by a re-fetch just to learn the new token. It
    now advances on both branches (FR-38, issue #300): a `designation` row
    has no version of its own, but amending one bumps the entry's counter
    via `nptc.catalogue.entries.entry_child_write`, the same way the
    preferred-term branch's `save_entry` always has.
    """

    model_config = ConfigDict(frozen=True)

    designation: Designation
    warnings: list[CollisionWarning]
    row_version: int


class RetireDesignationRequest(_WithLanguage):
    model_config = ConfigDict(frozen=True)

    term: str
    reason: str
    #: FR-38 (issue #300): see `AddDesignationsRequest`'s own field for why
    #: this is required rather than optional.
    expected_row_version: int = Field(ge=1)


class RetireDesignationResult(BaseModel):
    """`retire_designation_route`'s response: the retired row, plus the
    entry's new `row_version` (FR-38, issue #300).

    Declared here rather than returning a bare `Designation`, which has
    nowhere to carry `row_version` - `Designation` is the shared public read
    model (`catalogue_shared.py`) and must not grow an admin-only field,
    matching `catalogue_bindings.py`'s own `BindingWriteResult` precedent."""

    model_config = ConfigDict(frozen=True)

    designation: Designation
    row_version: int


class AcknowledgeCollisionRequest(_WithLanguage):
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
    exact `(entry, term, language)` was already acknowledged, by an earlier
    call" (`False`) - `acknowledge_collision` is idempotent (see its own
    docstring), and without this flag a caller cannot tell those two cases
    apart, nor notice that `reason` below is the *original* note rather
    than the one this call just submitted (issue #224 review finding 5)."""

    model_config = ConfigDict(frozen=True)

    language: str
    reason: str
    created: bool


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))
#: Declared as a value dependency, not just `dependencies=[...]`: unlike
#: the other three routes, this one has to pass the resolved `Principal`
#: through to `acknowledge_collision(acknowledger=...)` - `permission_dep`
#: already returns the checked `Principal`, so capturing it here does the
#: permission check and supplies the value in one dependency, rather than
#: resolving the principal a second time.
AcknowledgerDep = Annotated[Principal, Depends(permission_dep(Permission.VALIDATION_ACKNOWLEDGE))]


@router.post(
    "/entries/{business_key}/designations",
    summary="Add one or more synonyms, or a non-en-AU preferred term, to a catalogue entry",
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
    # One `entry_child_write` around the whole batch, not one per term: a
    # multi-term add is one logical write, so a failure part-way rolls the
    # whole batch back and bumps `catalogue_entry.row_version` once, not once
    # per term (FR-38, issue #300; pinned by
    # `test_a_batch_add_bumps_row_version_once_not_once_per_term`).
    #
    # This nests `add_synonyms`'/`add_designation`'s own collision-key
    # `pg_advisory_xact_lock` (`assert_no_error_collisions`) *inside* the
    # append lock `entry_child_write` takes first - and, since issue #281,
    # `add_designation` also takes the append lock itself, as its own first
    # statement, before this call ever reaches it. That is a safe,
    # re-entrant no-op re-assertion of the lock `entry_child_write` already
    # holds, not a second acquisition, and it is what keeps this route
    # correctly ordered (append lock, then collision lock) regardless of
    # whether some future caller reaches `add_designation`/`add_synonyms`
    # without going through `entry_child_write` at all - `nptc.catalogue.
    # entries.save_entry`/`create_entry` take the same append-lock-first
    # order today, for the same reason. See ADR-0035's "Lock ordering"
    # addendum for the fuller history of why this pair of locks needed a
    # single, consistent order in the first place.
    with entry_child_write(session, entry, body.expected_row_version):
        if body.use is DesignationUse.PREFERRED:
            # add_synonyms is synonym-only (it hardcodes use="synonym") and a
            # preferred variant is always a single term - `add_designation`
            # directly, matching the batch-of-one case add_synonyms would
            # otherwise reduce to.
            created = [
                add_designation(
                    session,
                    ctx,
                    entry=entry,
                    term=body.terms[0],
                    use=body.use,
                    language=body.language,
                    reason=body.reason,
                )
            ]
        else:
            created = add_synonyms(
                session,
                ctx,
                entry=entry,
                terms=body.terms,
                language=body.language,
                reason=body.reason,
            )
    response.headers["Location"] = f"{API_PREFIX}{router.prefix}/entries/{business_key}"
    created_ids = {designation.id for designation in created}
    rows = [
        row
        for row in queries.load_designations_any_status(session, (entry.id,))
        if row.id in created_ids
    ]
    # `warning_collisions` only ever looks for another live entry's active
    # *synonym* under the same key - meaningless for the preferred branch,
    # since a preferred term matching another entry's synonym is already an
    # *error*-severity collision `add_designation` would have raised before
    # reaching this line (issue #224 review, minor).
    warnings = (
        ()
        if body.use is DesignationUse.PREFERRED
        else warning_collisions(
            session,
            entry=entry,
            terms=[designation.term for designation in created],
            language=body.language,
        )
    )
    return DesignationWriteResult(
        designations=[designation_from_row(row) for row in rows],
        warnings=[_collision_warning(warning) for warning in warnings],
        row_version=entry.row_version,
    )


def _targets_preferred_term(entry: CatalogueEntry, body: AmendDesignationRequest) -> bool:
    """Whether this request means the entry's *own* en-AU preferred term
    rather than one of its `designation` rows (ADR-0022's other storage
    home).

    Only ever true for `en-AU`: a `preferred` designation in another
    language is a real row, and `ck_designation_no_en_au_preferred` is what
    guarantees there is no en-AU one to be confused with.

    `term` always has to name it, `use` or no `use`. The comparison is
    against the stored, indexed `preferred_term_key` column rather than a
    key recomputed from `entry.preferred_term`: that column is written by
    `CatalogueEntry`'s own `@validates("preferred_term")` hook from the same
    `collision_key(clean_term(...))` composition used here, so it cannot
    drift, and `nptc.catalogue.collisions`' module docstring makes "never
    recompute a key for something already stored" this package's rule. The
    fold means a caller naming a case or punctuation variant resolves the
    preferred term exactly as it would resolve a designation
    (`load_active_designation` keys on `term_key` for the same reason).

    **`use="preferred"` narrows which storage home to look in; it does not
    excuse the caller from naming the term** (issue #227 review). `term` is
    a required field whose documented job on this route is to address the
    thing being edited, and a branch that quietly disregarded it would be
    the same silent-wrong-target defect `use` was added to close - a
    mistyped `term` alongside `use="preferred"` would rename the preferred
    term rather than 404. Requiring the match costs the escape hatch
    nothing: in the case `use` exists for, the shadowing synonym folds to
    the *same* `collision_key` as the preferred term by definition - that is
    what makes it a shadow - so a caller reaching past it always names a
    matching term anyway. What `use` actually buys is skipping the
    designation lookup, which is what lets the preferred term be reached at
    all once a synonym shadows it.

    (An earlier revision skipped the comparison here, reasoning that an
    entry has exactly one preferred term so naming it adds nothing, by
    analogy with `POST .../designations` under `use=preferred`. The analogy
    does not hold: there `term` is the *new value*, here it is the
    *address*.)

    `body.language` has already been canonicalised by `_WithLanguage`, so
    `en-au` is matched here too, not only `en-AU`.
    """
    if body.language != DEFAULT_LANGUAGE:
        return False
    if body.use is DesignationUse.SYNONYM:
        return False
    return entry.preferred_term_key == collision_key(clean_term(body.term))


def _preferred_term_as_designation(entry: CatalogueEntry) -> Designation:
    """The catalogue's own preferred term in the shape this API gives every
    other term. Not read back from a `designation` row, because ADR-0022
    guarantees there is never one to read (`ck_designation_no_en_au_
    preferred`); the constant `use`/`language`/`status` here are that
    invariant restated, not a fact about a row.

    `label_provenance` is `AU_PREFERRED_TERM_PROVENANCE`, not
    `designation_from_row`'s own `use="preferred"` mapping to
    `PREFERRED_VARIANT` (FR-98, issue #144): the term this function wraps
    is `entry.preferred_term` in `DEFAULT_LANGUAGE` (en-AU) - the
    catalogue's own AU preferred term, matching `EntrySummary.
    preferred_term`'s own designation type - not a non-en-AU preferred
    variant from a `designation` row. `use="preferred"` here is the shape
    this API gives every term, not the fact this provenance is about.
    """
    return Designation(
        term=entry.preferred_term,
        use=str(DesignationUse.PREFERRED),
        language=DEFAULT_LANGUAGE,
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
    business_key: BusinessKeyPath,
    body: Annotated[AmendDesignationRequest, Body()],
) -> AmendDesignationResult:
    """One route, two storage homes (issue #227).

    `use="preferred"` with the default `en-AU` addresses the entry's own
    preferred term outright. Otherwise `term` resolves against an active
    `designation` row first, falling back to the preferred term only where
    there is no such row - see the module docstring for why that fallback
    order is designation-first, and why the explicit `use` exists at all.
    """
    entry = load_entry_for_update(session, business_key)
    designation = (
        None
        if body.use is DesignationUse.PREFERRED and body.language == DEFAULT_LANGUAGE
        # ADR-0022: there is no en-AU preferred designation row to find, so
        # the lookup is skipped rather than run and discarded.
        else find_active_designation(
            session, entry_id=entry.id, term=body.term, language=body.language
        )
    )

    if designation is None and _targets_preferred_term(entry, body):
        save_entry(
            session,
            ctx,
            business_key=business_key,
            expected_row_version=body.expected_row_version,
            changes=EntryChanges(preferred_term=body.new_term),
            reason=body.reason,
        )
        session.flush()
        # No `warnings`, for the same reason `add_designations`' preferred
        # branch has none: `warning_collisions` only ever looks for another
        # live entry's active *synonym*, and a preferred term matching one
        # is an *error*-severity collision `save_entry` has already raised.
        return AmendDesignationResult(
            designation=_preferred_term_as_designation(entry),
            warnings=[],
            row_version=entry.row_version,
        )

    # The 404 this route has always given an unresolvable term sits inside
    # the lock (FR-38, issue #300): `entry_child_write`'s own version check
    # runs first, so a stale caller sees the version conflict rather than a
    # confusing 404 - the same choice `catalogue_bindings.py`'s
    # `retire_binding` documents for its own lookup.
    #
    # `amend_designation` below also takes the collision-key advisory lock
    # (`assert_no_error_collisions`), and (since issue #281) the append lock
    # itself first, as its own first statement - a safe re-assertion of the
    # lock `entry_child_write` already holds, not a second acquisition. See
    # `add_designations`' own comment above and ADR-0035's "Lock ordering"
    # addendum for the fuller history.
    with entry_child_write(session, entry, body.expected_row_version):
        if designation is None:
            # Raised here rather than by calling `load_active_designation`
            # for its refusal, which would re-run the identical `SELECT`
            # purely to fail (issue #227 review). The message is for the
            # log only - the handler never echoes `str(exc)` - so it says
            # what this route actually checked, which is more than that
            # function would know.
            raise DesignationNotFoundError(
                f"entry {entry.id} has no active designation for term {body.term!r} in "
                f"language {body.language!r}, and it is not the entry's own preferred term"
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
    # See `add_designations`' own comment: meaningless for a preferred
    # designation (issue #224 review, minor).
    warnings = (
        ()
        if amended.use == str(DesignationUse.PREFERRED)
        else warning_collisions(session, entry=entry, terms=[amended.term], language=body.language)
    )
    return AmendDesignationResult(
        designation=designation_from_row(row),
        warnings=[_collision_warning(warning) for warning in warnings],
        # The entry's, bumped by `entry_child_write` above - a `designation`
        # row has no version of its own, so this write takes the entry's
        # lock instead (FR-38, issue #300).
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
    # `load_active_designation` (a 404 for a missing/retired term) runs
    # inside the lock, after `entry_child_write`'s own version check - a
    # stale caller sees the version conflict, not an unrelated 404, matching
    # `catalogue_bindings.py`'s `retire_binding` precedent (FR-38, issue #300).
    with entry_child_write(session, entry, body.expected_row_version):
        designation = load_active_designation(
            session, entry_id=entry.id, term=body.term, language=body.language
        )
        _retire_designation(session, ctx, designation=designation, reason=body.reason)
    designation_id = designation.id
    row = queries.load_designation_by_id(session, designation_id)
    if row is None:
        raise RuntimeError(f"designation {designation_id} not found immediately after retirement")
    return RetireDesignationResult(
        designation=designation_from_row(row), row_version=entry.row_version
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
        language=body.language,
        reason=body.reason,
    )
    session.flush()
    return CollisionAcknowledgementResponse(
        language=acknowledgement.language,
        reason=acknowledgement.reason,
        created=created,
    )
