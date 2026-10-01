"""Write routes over `code_binding` (FR-06, FR-08, FR-36, NFR-08).

The HTTP adapter over `nptc.catalogue.bindings`; it re-implements no domain rule. A domain
exception carries `http_status` and `nptc.api.errors` maps it, so no route body has a
try/except. `docs/architecture/catalogue-write-api.md` ("Code bindings") records the
reasoning behind the points below.

**A separate router from `catalogue.py`.** That module is the public read surface (FR-20),
and `test_api_public_response_hygiene.py` derives its endpoint list from its route table.
Adding a POST there would change what that test covers without anyone noticing.

**A binding is addressed by `code`, never an id.** The public `Binding` carries no `id` or
`entry_id`. `ix_code_binding_one_active_per_entry` guarantees at most one active binding
matches an `(entry, code)` pair, and `nptc.catalogue.bindings.load_active_binding` resolves
it. `system` is not on the wire: every route uses `SNOMED_CT_SYSTEM`, the only system in
use, and exposing a second one later is additive.

**Replacement is one route, not three.** Retire, create and link must run in that order,
because `ix_code_binding_one_active_per_entry` forbids a successor existing active while its
predecessor still is. As three HTTP calls, a failed second request would strand an entry
with no active binding and no successor. `replace_binding` runs all three in the one
request transaction (committed by `get_session`), so all three audit events land or none do.

**Authorisation:** every route requires `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44), held
only by `Role.ADMINISTRATOR` and in `MFA_REQUIRED_PERMISSIONS` (NFR-06). An administrator
without a completed step-up gets the RFC 9470 challenge.

**Every write requires `expected_row_version` (FR-38).** `code_binding` has no version
column, so all three routes lock on `catalogue_entry.row_version` through
`nptc.catalogue.entries.entry_child_write`, as `save_property_values` does for
`property_value`. `replace_binding` takes the lock once, before its three writes, so a stale
version leaves no partial replacement and no audit event. Each response echoes the entry's
new `row_version` (`BindingWriteResult`, `BindingReplacementResult`).

**Concurrent writes on one entry.** `entry_child_write` takes the audit advisory lock first.
Writers that do not collide on an index (different codes, or a bind racing a retire)
serialise on it, and the loser's version bump finds `row_version` moved and becomes a 409.
Writers targeting the same code hit the partial unique index first, because
`create_binding`'s audit append flushes its `INSERT` before the bump, and surface the
index's domain error. `entry_child_write`'s docstring describes the two layers.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import ApiSettingsDep, AuditContextDep, get_session, permission_dep
from nptc.api.errors import VersionConflictResponse
from nptc.api.prefix import API_PREFIX
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    Binding,
    BusinessKeyPath,
    binding_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.bindings import (
    CodeBindingSelfSupersessionError,
    CodeBindingWriteNotFoundError,
    create_binding,
    link_replacement,
    load_active_binding,
)
from nptc.catalogue.bindings import retire_binding as _retire_binding
from nptc.catalogue.entries import entry_child_write, load_entry_for_update
from nptc.db.models.code_binding import CodeBindingEditionHint
from nptc.settings import ApiSettings

router = APIRouter(prefix="/catalogue", tags=["catalogue-admin"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}

_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `catalogue.edit_published`, "
        "or holds it but has not completed the MFA step-up this permission requires "
        "(the response then also carries a `WWW-Authenticate` step-up challenge)."
    ),
}

_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No catalogue entry, or no active code binding, matches the given identifier.",
}

#: Two 409 causes reach a caller: a domain conflict (`ErrorResponse`) and a stale
#: `expected_row_version` (`VersionConflictResponse`, FR-38). `model` takes the
#: union so both land in `components/schemas`, as in `catalogue_properties.py`.
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse | VersionConflictResponse,
    "description": (
        "The request is well-formed but conflicts with the current state of the "
        "system - a second active binding on this entry, a successor code already "
        "actively bound elsewhere (including two concurrent requests racing for "
        "the same entry or code), `/replacement`'s successor naming the same code "
        "it is meant to replace, or a stale `expected_row_version` (FR-38) because "
        "someone else changed this entry since it was loaded - that variant carries "
        "`business_key`, `expected_row_version`, `current_row_version`, `conflicts[]` "
        "and `changed_by`/`changed_at`, so the caller can reconcile rather than "
        "retry blind. A code already retired, or with no binding at all, is a 404 "
        "here rather than a 409: every route below addresses a binding by its "
        "currently-*active* code, so a retired one is simply not addressable this "
        "way any more, not a conflicting state."
    ),
}

_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A field failed validation - a malformed or Verhoeff-failing SCTID, an "
        "unrecognised edition hint, a blank `fsn`/`au_preferred_term`, or a "
        "changelog note that does not meet FR-37."
    ),
}

_RESPONSE_500: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A platform-side invariant failed, not a caller mistake - e.g. re-reading "
        "a binding this same request just wrote could not find it. Not produced "
        "by anything a well-formed request can trigger on its own; retrying will "
        "not clear it."
    ),
}

BINDING_WRITE_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    500: _RESPONSE_500,
}

#: `bind_code` alone: its 201 carries a `Location` header naming the entry the
#: binding was added to, because no route serves a binding on its own. Declared so
#: a reader of `docs/api/openapi.json` learns of it without reading the route body.
_BIND_CODE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **BINDING_WRITE_ERROR_RESPONSES,
    201: {
        "headers": {
            "Location": {
                "description": "The entry (`GET {business_key}`) the new binding was added to.",
                "schema": {"type": "string"},
            }
        }
    },
}


def _reject_blank(value: str | None) -> str | None:
    """Shared by every `fsn`/`au_preferred_term` field below. `min_length=1` lets a
    whitespace-only string through, and `ck_code_binding_fsn_not_blank` and
    `ck_code_binding_au_preferred_term_not_blank` check `btrim(...)`, so such a value
    would 500 on an unmapped `IntegrityError` instead of a 422. Rejects, never strips:
    FR-82 forbids cleaning these values."""
    if value is not None and not value.strip():
        raise ValueError("must not be blank")
    return value


class BindCodeRequest(BaseModel):
    """The body of `POST /catalogue/entries/{business_key}/bindings`.

    `code` is a string end-to-end (FR-06) - `nptc.catalogue.bindings.
    create_binding` validates it via `nptc_shared.sctid.SCTID` before any
    row is touched. `fsn`/`au_preferred_term` are carried through exactly
    as submitted (FR-82); this screen is the one place they are meant to be
    typed in, and neither is cleaned or re-derived here or anywhere else.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    fsn: str = Field(min_length=1)
    au_preferred_term: str | None = Field(default=None, min_length=1)
    edition_hint: CodeBindingEditionHint = CodeBindingEditionHint.UNKNOWN
    reason: str
    expected_row_version: int = Field(ge=1)

    _reject_blank_fsn = field_validator("fsn")(_reject_blank)
    _reject_blank_au_preferred_term = field_validator("au_preferred_term")(_reject_blank)


class RetireBindingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str
    expected_row_version: int = Field(ge=1)


class ReplacementSuccessor(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    fsn: str = Field(min_length=1)
    au_preferred_term: str | None = Field(default=None, min_length=1)
    edition_hint: CodeBindingEditionHint = CodeBindingEditionHint.UNKNOWN

    _reject_blank_fsn = field_validator("fsn")(_reject_blank)
    _reject_blank_au_preferred_term = field_validator("au_preferred_term")(_reject_blank)


class ReplaceBindingRequest(BaseModel):
    """One `reason` covers all three steps of the replacement (retire,
    create, link) - a caller explaining *why* a code is being replaced is
    explaining one editorial decision, not three. `expected_row_version`
    likewise guards all three as one lock, taken once - see the module
    docstring's FR-38 note."""

    model_config = ConfigDict(frozen=True)

    successor: ReplacementSuccessor
    reason: str
    expected_row_version: int = Field(ge=1)


class BindingWriteResult(BaseModel):
    """`bind_code`/`retire_binding`'s response: the affected binding, plus
    the entry's new `row_version` - mirroring `catalogue_properties.
    PropertyValuesWriteResult`, so a client never has to re-fetch the entry
    just to learn its next lock token. Declared here, not in
    `catalogue_shared.py`: that module's `Binding`/`BindingList` are shared
    with the *public* read route (`catalogue.py`), and widening them with
    an admin-only `row_version` field would break
    `test_api_public_response_hygiene.py`."""

    model_config = ConfigDict(frozen=True)

    binding: Binding
    row_version: int


class BindingReplacementResult(BaseModel):
    """`replace_binding`'s response: both affected bindings (the retired
    predecessor and its successor), plus the entry's new `row_version` -
    see `BindingWriteResult`'s own docstring for why this is declared here
    rather than reusing the public `BindingList`."""

    model_config = ConfigDict(frozen=True)

    items: list[Binding]
    row_version: int


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.post(
    "/entries/{business_key}/bindings",
    summary="Bind a SNOMED CT code to a catalogue entry",
    status_code=201,
    responses=_BIND_CODE_RESPONSES,
    dependencies=[_EDIT],
)
def bind_code(
    session: SessionDep,
    ctx: AuditContextDep,
    settings: ApiSettingsDep,
    response: Response,
    business_key: BusinessKeyPath,
    body: Annotated[BindCodeRequest, Body()],
) -> BindingWriteResult:
    entry = load_entry_for_update(session, business_key)
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        binding = create_binding(
            session,
            ctx,
            entry=entry,
            code=body.code,
            fsn=body.fsn,
            au_preferred_term=body.au_preferred_term,
            edition_hint=body.edition_hint,
            reason=body.reason,
        )
    # Not `.../bindings/{code}`: nothing serves a `GET` there, so a client could not
    # follow it. The entry detail route exists and shows the new binding.
    response.headers["Location"] = f"{API_PREFIX}{router.prefix}/entries/{business_key}"
    return BindingWriteResult(
        binding=_row_to_binding(session, settings, entry_id=entry.id, binding_id=binding.id),
        row_version=entry.row_version,
    )


@router.post(
    "/entries/{business_key}/bindings/{code}/retirement",
    summary="Retire an entry's active code binding",
    responses=BINDING_WRITE_ERROR_RESPONSES,
    dependencies=[_EDIT],
)
def retire_binding(
    session: SessionDep,
    ctx: AuditContextDep,
    settings: ApiSettingsDep,
    business_key: BusinessKeyPath,
    code: str,
    body: Annotated[RetireBindingRequest, Body()],
) -> BindingWriteResult:
    entry = load_entry_for_update(session, business_key)
    # `load_active_binding` (a 404 for a missing or retired code) runs inside the
    # lock, after the version check, so a stale caller sees the conflict, not a 404,
    # as `save_entry` does.
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        binding = load_active_binding(session, entry_id=entry.id, code=code)
        _retire_binding(session, ctx, binding=binding, reason=body.reason)
    return BindingWriteResult(
        binding=_row_to_binding(session, settings, entry_id=entry.id, binding_id=binding.id),
        row_version=entry.row_version,
    )


@router.post(
    "/entries/{business_key}/bindings/{code}/replacement",
    summary="Retire an entry's active code binding and bind its successor",
    responses=BINDING_WRITE_ERROR_RESPONSES,
    dependencies=[_EDIT],
)
def replace_binding(
    session: SessionDep,
    ctx: AuditContextDep,
    settings: ApiSettingsDep,
    business_key: BusinessKeyPath,
    code: str,
    body: Annotated[ReplaceBindingRequest, Body()],
) -> BindingReplacementResult:
    """Runs `retire_binding` -> `create_binding` -> `link_replacement` in
    that order, inside this request's one transaction (see the module
    docstring) - `code`/`fsn`/etc. of the successor are the caller's own,
    exactly like `bind_code` above.

    The self-supersession refusal below runs first and needs no state - it
    is checked before `entry_child_write` even takes the lock, unlike the
    three writes it guards against. Those three then share **one**
    `entry_child_write`, taken once before any of them: a stale
    `expected_row_version` refuses before `retire_binding` ever runs, so a
    stale caller can never strand this entry mid-replacement (FR-38, issue
    #60)."""
    if body.successor.code == code:
        # `link_replacement`'s self-supersession check compares row *identity*,
        # which a same-code replacement never trips: `create_binding` would insert a
        # second row with the same code, and both `_row_to_binding` lookups below
        # would resolve to the active row, reporting the successor twice and never
        # the retirement.
        raise CodeBindingSelfSupersessionError(f"code {code!r} cannot be replaced by itself")
    entry = load_entry_for_update(session, business_key)
    with entry_child_write(session, entry, body.expected_row_version, reason=body.reason):
        superseded = load_active_binding(session, entry_id=entry.id, code=code)
        _retire_binding(session, ctx, binding=superseded, reason=body.reason)
        successor = create_binding(
            session,
            ctx,
            entry=entry,
            code=body.successor.code,
            fsn=body.successor.fsn,
            au_preferred_term=body.successor.au_preferred_term,
            edition_hint=body.successor.edition_hint,
            reason=body.reason,
        )
        link_replacement(
            session, ctx, superseded=superseded, successor=successor, reason=body.reason
        )
    return BindingReplacementResult(
        items=[
            _row_to_binding(session, settings, entry_id=entry.id, binding_id=superseded.id),
            _row_to_binding(session, settings, entry_id=entry.id, binding_id=successor.id),
        ],
        row_version=entry.row_version,
    )


def _row_to_binding(
    session: Session, settings: ApiSettings, *, entry_id: uuid.UUID, binding_id: uuid.UUID
) -> Binding:
    """Re-reads the just-written row through `nptc.catalogue.queries.load_bindings`,
    not from the ORM instance. That resolves `replaced_by_binding_id` to the
    successor's *code* (no internal id reaches a response model) and attaches
    `label_provenance` through `binding_from_row`, so a bound code renders the same
    whether just written or freshly read.

    Keyed on `binding_id`, not `code`: `(entry_id, code)` is unique only among
    *active* bindings, so a code bound, retired and bound again leaves two retired
    rows a code-keyed lookup cannot tell apart."""
    for row in queries.load_bindings(session, (entry_id,)):
        if row.id == binding_id:
            return binding_from_row(row, settings)
    raise CodeBindingWriteNotFoundError(
        f"just-written code binding {binding_id} not found on re-read"
    )
