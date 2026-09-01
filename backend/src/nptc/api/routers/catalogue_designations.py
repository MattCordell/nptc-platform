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

**The preferred-term branch requires `expected_row_version`; the designation
branch merely honours it.** `catalogue_entry` is a row with FR-38 optimistic
locking (`nptc.catalogue.entries.save_entry`), so a write to it cannot be
accepted without the caller's version - a 422 without one
(`PreferredTermVersionRequiredError`, which cannot be a pydantic validator:
which storage home a term lives in is a database question). A `designation`
row has no version of its own, so the field stays optional there rather than
breaking every client this route has shipped with since #224 - but it is
checked against `catalogue_entry.row_version` whenever it is supplied
(`assert_entry_row_version`), because silently discarding a caller's lock
token is worse than either honouring it or refusing it. That is a partial
answer to the concurrency gap `docs/architecture/catalogue-write-api.md`
names: it is opt-in, and amending a designation does not itself bump the
entry's version, so two administrators editing different designations still
do not conflict.

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
from nptc.api.errors import (
    DesignationCollisionResponse,
    PreferredTermVersionRequiredError,
    VersionConflictResponse,
)
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
    assert_entry_row_version,
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
_RESPONSE_409_AMENDMENT: Final[dict[str, Any]] = {
    **_RESPONSE_409_COLLISION,
    "model": ErrorResponse | DesignationCollisionResponse | VersionConflictResponse,
    "description": (
        f"{_RESPONSE_409_COLLISION['description']} A stale `expected_row_version` "
        "(FR-38) carries `business_key`, `expected_row_version`, "
        "`current_row_version`, `conflicts[]` (each with `field`, `submitted` and "
        "`current`) and `changed_by`/`changed_at`, so the caller can reconcile "
        "rather than retry blind."
    ),
}
_RESPONSE_422_AMENDMENT: Final[dict[str, Any]] = {
    **_RESPONSE_422,
    "description": (
        f"{_RESPONSE_422['description']} Also a `term` naming the entry's own "
        "preferred term with no `expected_row_version` to save it under (FR-38): "
        "the field is optional in the schema because it is required on only that "
        "one branch, which a schema cannot express."
    ),
}

#: Shared by add/amend/retire: all three are gated on the same permission
#: and can fail with the same set of statuses. One constant, not three
#: identical dicts, so a future divergence between them is a deliberate
#: edit rather than an accident of copy-paste (issue #224 review, minor).
_RESPONSES_WRITE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_EDIT,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}
#: Add: can collide (FR-05), cannot version-conflict.
_RESPONSES_ADD: Final[dict[int | str, dict[str, Any]]] = {
    **_RESPONSES_WRITE,
    409: _RESPONSE_409_COLLISION,
}
#: Amend: can do both, and is the only route here that writes an entry.
_RESPONSES_AMEND: Final[dict[int | str, dict[str, Any]]] = {
    **_RESPONSES_WRITE,
    409: _RESPONSE_409_AMENDMENT,
    422: _RESPONSE_422_AMENDMENT,
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
    handler for the *error*-severity case already returns."""

    model_config = ConfigDict(frozen=True)

    term: str
    business_key: str
    preferred_term: str


def _collision_warning(collision: Collision) -> CollisionWarning:
    return CollisionWarning(
        term=collision.term,
        business_key=collision.business_key,
        preferred_term=collision.preferred_term,
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
    model_config = ConfigDict(frozen=True)

    designations: list[Designation]
    warnings: list[CollisionWarning]


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
    #: (issue #227). Optional in the schema and conditionally required in
    #: fact: mandatory when `term` addresses the entry's own preferred term
    #: (a 422 without it - `nptc.api.errors.
    #: PreferredTermVersionRequiredError`, which explains why this cannot be
    #: a `model_validator`), optional when it addresses a designation row.
    #:
    #: Optional rather than required outright because making it required
    #: would break every client of the designation branch this route has
    #: shipped with since #224. Enforced whenever supplied rather than
    #: ignored on the branch that does not demand it, because silently
    #: discarding a caller's lock token is worse than either honouring it or
    #: refusing it - a client that sent one believes it is protected.
    expected_row_version: int | None = Field(default=None, ge=1)


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
    never has to be followed by a re-fetch just to learn the new token. On
    the designation branch it is unchanged by the write: a `designation`
    row has no version of its own, and amending one does not bump the
    entry's (see the module docstring on what taking the entry's lock here
    does and does not buy).
    """

    model_config = ConfigDict(frozen=True)

    designation: Designation
    warnings: list[CollisionWarning]
    row_version: int


class RetireDesignationRequest(_WithLanguage):
    model_config = ConfigDict(frozen=True)

    term: str
    reason: str


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
    session.flush()
    response.headers["Location"] = f"{API_PREFIX}{router.prefix}/entries/{business_key}"
    created_ids = {designation.id for designation in created}
    rows = [
        row
        for row in queries.load_designations_for_write(session, (entry.id,))
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
    invariant restated, not a fact about a row."""
    return Designation(
        term=entry.preferred_term,
        use=str(DesignationUse.PREFERRED),
        language=DEFAULT_LANGUAGE,
        status=str(DesignationStatus.ACTIVE),
        length=entry.length,
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
        if body.expected_row_version is None:
            raise PreferredTermVersionRequiredError(
                f"amending {business_key}'s own preferred term requires expected_row_version"
            )
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

    if designation is None:
        # The 404 this route has always given an unresolvable term. Raised
        # here rather than by calling `load_active_designation` for its
        # refusal, which would re-run the identical `SELECT` purely to fail
        # (issue #227 review). The message is for the log only - the handler
        # never echoes `str(exc)` - so it says what this route actually
        # checked, which is more than that function would know.
        raise DesignationNotFoundError(
            f"entry {entry.id} has no active designation for term {body.term!r} in "
            f"language {body.language!r}, and it is not the entry's own preferred term"
        )

    if body.expected_row_version is not None:
        assert_entry_row_version(session, entry, body.expected_row_version)

    amended = amend_designation(
        session,
        ctx,
        entry=entry,
        designation=designation,
        new_term=body.new_term,
        reason=body.reason,
    )
    session.flush()
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
        # The entry's, unchanged by this write - a `designation` row has no
        # version of its own. Returned anyway so both branches hand a client
        # the same token, rather than making it know which storage home it
        # just wrote to in order to know whether to re-read.
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
) -> Designation:
    entry = load_entry_for_update(session, business_key)
    designation = load_active_designation(
        session, entry_id=entry.id, term=body.term, language=body.language
    )
    _retire_designation(session, ctx, designation=designation, reason=body.reason)
    session.flush()
    designation_id = designation.id
    row = queries.load_designation_by_id(session, designation_id)
    if row is None:
        raise RuntimeError(f"designation {designation_id} not found immediately after retirement")
    return designation_from_row(row)


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
