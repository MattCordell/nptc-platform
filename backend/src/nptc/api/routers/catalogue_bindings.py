"""Write routes over `code_binding` (issue #219, FR-06, FR-08, FR-36, NFR-08).

The first state-changing HTTP surface in this repository. Everything it
calls already exists and is already tested as a library -
`nptc.catalogue.bindings` (issue #48) - so nothing here re-implements a
domain rule; this module is purely the HTTP adapter, following
`routers/catalogue.py`'s own house style: response models declared here,
`ConfigDict(frozen=True)`, the return-type annotation drives the response
model (never `response_model=`), module-level `Final` error-response dicts
naming only the statuses a route can actually produce, and no try/except in
a route body - a domain exception carries `http_status` and is mapped
centrally by `nptc.api.errors`.

**A separate router from `catalogue.py`, on purpose.** That module's own
docstring declares itself the public *read* surface (FR-20) and
`test_api_public_response_hygiene.py` derives its endpoint list from its
route table - folding a POST into it would silently widen what that test
covers, or worse, silently not. Same `/catalogue` path space, its own tag.

**Addressing a binding: by `code`, never an id.** The public `Binding`
model deliberately carries no `id`/`entry_id` (see `catalogue.py`), so a
client retiring or replacing one addresses it by the SNOMED CT code it was
bound with - `ix_code_binding_one_active_entry_per_code` guarantees at most
one *active* binding matches, and `nptc.catalogue.bindings.
load_active_binding` is what resolves that. `system` is not on the wire at
all; every route here defaults to `SNOMED_CT_SYSTEM`, the only system in
use today - additive to expose later if a second system is ever needed.

**Replacement is one route, not three.** `nptc.catalogue.bindings`' module
docstring explains why replacing a binding is a three-step sequence
(retire, create, link) rather than one function:
`ix_code_binding_one_active_per_entry` forbids a successor existing active
while its predecessor still is. Exposing that as three HTTP calls would let
a client's failed second request strand an entry with no active binding
and no successor - so `replace_binding` below runs all three inside the
one request's `session_scope` transaction (committed together by
`get_session`, issue #41), and all three audit events land or none do.

**Authorisation:** every route requires `Permission.CATALOGUE_EDIT_PUBLISHED`
(FR-44) - held only by `Role.ADMINISTRATOR` and in `MFA_REQUIRED_PERMISSIONS`
(NFR-06), so an administrator who has not completed a step-up gets the
RFC 9470 challenge for free, the same as any other MFA-gated permission.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.dependencies import AuditContextDep, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue import Binding, BindingList, BusinessKeyPath, _binding
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.bindings import create_binding, link_replacement, load_active_binding
from nptc.catalogue.bindings import retire_binding as _retire_binding
from nptc.catalogue.entries import load_entry_for_update
from nptc.db.models.code_binding import CodeBindingEditionHint

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

_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The request is well-formed but conflicts with the current state of the "
        "system - a second active binding on this entry, or a successor code "
        "already actively bound elsewhere. A code already retired, or with no "
        "binding at all, is a 404 here rather than a 409: every route below "
        "addresses a binding by its currently-*active* code, so a retired one is "
        "simply not addressable this way any more, not a conflicting state."
    ),
}

_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A field failed validation - a malformed or Verhoeff-failing SCTID, an "
        "unrecognised edition hint, or a changelog note that does not meet FR-37."
    ),
}

BINDING_WRITE_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}


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
    fsn: str
    au_preferred_term: str | None = None
    edition_hint: str = str(CodeBindingEditionHint.UNKNOWN)
    reason: str


class RetireBindingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str


class ReplacementSuccessor(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    fsn: str
    au_preferred_term: str | None = None
    edition_hint: str = str(CodeBindingEditionHint.UNKNOWN)


class ReplaceBindingRequest(BaseModel):
    """One `reason` covers all three steps of the replacement (retire,
    create, link) - a caller explaining *why* a code is being replaced is
    explaining one editorial decision, not three."""

    model_config = ConfigDict(frozen=True)

    successor: ReplacementSuccessor
    reason: str


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.post(
    "/entries/{business_key}/bindings",
    summary="Bind a SNOMED CT code to a catalogue entry",
    status_code=201,
    responses=BINDING_WRITE_ERROR_RESPONSES,
    dependencies=[_EDIT],
)
def bind_code(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    body: Annotated[BindCodeRequest, Body()],
) -> Binding:
    entry = load_entry_for_update(session, business_key)
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
    session.flush()
    return _row_to_binding(session, entry_id=entry.id, code=binding.code)


@router.post(
    "/entries/{business_key}/bindings/{code}/retirement",
    summary="Retire an entry's active code binding",
    responses=BINDING_WRITE_ERROR_RESPONSES,
    dependencies=[_EDIT],
)
def retire_binding(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    code: str,
    body: Annotated[RetireBindingRequest, Body()],
) -> Binding:
    entry = load_entry_for_update(session, business_key)
    binding = load_active_binding(session, entry_id=entry.id, code=code)
    _retire_binding(session, ctx, binding=binding, reason=body.reason)
    session.flush()
    return _row_to_binding(session, entry_id=entry.id, code=code)


@router.post(
    "/entries/{business_key}/bindings/{code}/replacement",
    summary="Retire an entry's active code binding and bind its successor",
    responses=BINDING_WRITE_ERROR_RESPONSES,
    dependencies=[_EDIT],
)
def replace_binding(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    code: str,
    body: Annotated[ReplaceBindingRequest, Body()],
) -> BindingList:
    """Runs `retire_binding` -> `create_binding` -> `link_replacement` in
    that order, inside this request's one transaction (see the module
    docstring) - `code`/`fsn`/etc. of the successor are the caller's own,
    exactly like `bind_code` above."""
    entry = load_entry_for_update(session, business_key)
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
    link_replacement(session, ctx, superseded=superseded, successor=successor, reason=body.reason)
    session.flush()
    return BindingList(
        items=[
            _row_to_binding(session, entry_id=entry.id, code=superseded.code),
            _row_to_binding(session, entry_id=entry.id, code=successor.code),
        ]
    )


def _row_to_binding(session: Session, *, entry_id: uuid.UUID, code: str) -> Binding:
    """Re-reads the just-written row through `nptc.catalogue.queries.
    load_bindings` rather than building a `Binding` from the ORM instance
    directly - that is what resolves `replaced_by_binding_id` to the
    successor's *code* (`catalogue.py`'s own rule: no internal id ever
    reaches a response model) and computes `display_term` via `_binding`,
    the same function the read routes use, so a bound code renders
    identically whether it was just written or freshly read."""
    for row in queries.load_bindings(session, (entry_id,)):
        if row.code == code:
            return _binding(row)
    raise AssertionError(f"just-written code binding {code!r} not found on re-read")
