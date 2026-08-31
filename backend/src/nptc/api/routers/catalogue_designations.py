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

**Preferred term is out of scope here (issue #224, phase 4 - still open).**
ADR-0022 keeps the catalogue's own en-AU preferred term on
`catalogue_entry.preferred_term`, never a `designation` row
(`ck_designation_no_en_au_preferred`); every route below only ever touches
`designation` rows. Amending `catalogue_entry.preferred_term` needs FR-38's
optimistic locking on the wire, which the public `EntryDetail`/`EntrySummary`
models do not carry yet - a follow-up, not folded in here.

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
from nptc.api.prefix import API_PREFIX
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import BusinessKeyPath, Designation, _designation
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal
from nptc.catalogue import queries
from nptc.catalogue.collisions import Collision, acknowledge_collision, warning_collisions
from nptc.catalogue.designations import (
    add_designation,
    add_synonyms,
    amend_designation,
    load_active_designation,
)
from nptc.catalogue.designations import retire_designation as _retire_designation
from nptc.catalogue.entries import load_entry_for_update
from nptc.catalogue.term_hygiene import clean_term, validate_language_tag
from nptc.db.models.designation import DesignationUse
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
                "amend the entry's preferred term directly instead (ADR-0022)"
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
    amend_designation`'s own choice - see that function's docstring."""

    model_config = ConfigDict(frozen=True)

    term: str
    new_term: str = Field(min_length=1)
    reason: str


class AmendDesignationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    designation: Designation
    warnings: list[CollisionWarning]


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
    responses=_RESPONSES_WRITE,
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
        designations=[_designation(row) for row in rows],
        warnings=[_collision_warning(warning) for warning in warnings],
    )


@router.post(
    "/entries/{business_key}/designations/amendment",
    summary="Edit an entry's active designation in place",
    responses=_RESPONSES_WRITE,
    dependencies=[_EDIT],
)
def amend_designation_route(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    body: Annotated[AmendDesignationRequest, Body()],
) -> AmendDesignationResult:
    entry = load_entry_for_update(session, business_key)
    designation = load_active_designation(
        session, entry_id=entry.id, term=body.term, language=body.language
    )
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
        designation=_designation(row),
        warnings=[_collision_warning(warning) for warning in warnings],
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
    return _designation(row)


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
