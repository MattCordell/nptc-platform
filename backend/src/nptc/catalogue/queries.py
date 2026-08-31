"""The public catalogue's read layer (issue #142, FR-20).

Everything the FR-20 API serves is read through this module. It is
deliberately separate from `entries.py`/`designations.py`/`bindings.py`,
which are write paths with domain rules attached: a read has exactly two
rules of its own, and both belong in one place.

**Rule one: `PUBLIC_STATUSES` is the only status filter, and it is one
tuple.** `active` and nothing else - not `draft` (unpublished), not
`withdrawn`, and not `deprecated` either. A deprecated entry is deliberately
absent rather than served-with-a-flag: the FR-20 surface is what a vendor
builds a request form from, and an entry that has been deprecated is
precisely one they must stop offering. Every query here imports the same
constant, so adding a query without the filter is a visible omission rather
than a plausible-looking `where` clause; `backend/tests/
test_api_public_status_filter.py` asserts the absence over every endpoint at
once.

**Rule two: an internal UUID never leaves this module.** `catalogue_entry.
id` is used here to batch the child loads and for nothing else, and
`code_binding.replaced_by_binding_id` is resolved to the *successor's code*
by a self-join (`load_bindings`) so the router never holds a UUID it could
serialise by accident. PRD SS6.2 makes `business_key` the only public
identifier; `nptc.auth.identity.UserRef` is the same boundary pattern
applied to `app_user`.

**No `relationship()`, anywhere.** There is none in `nptc.db.models`, and
this module does not introduce one: every association is an explicit
`select()`. Each loader takes a *collection* of entry ids and filters
`.in_(...)`, so the list endpoint issues a fixed number of queries no matter
the page size - a per-entry loader (lazy-loaded or otherwise) would make
page size and query count the same number, which is how a browse endpoint
becomes the slowest thing in a deployment.

Keyset pagination, never `OFFSET`: see `docs/adr/
0024-catalogue-search-and-pagination.md`. `list_entries` asks for one row
more than the caller wanted, and that extra row is what decides whether
there is a next page - so no endpoint here ever runs a `COUNT(*)` over the
catalogue to answer a question the client asked about one page.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from nptc.catalogue.errors import EntryNotFoundError
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation, DesignationStatus
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.property_value import PropertyValue

__all__ = [
    "PUBLIC_STATUSES",
    "BindingRow",
    "DesignationRow",
    "EntryPage",
    "PropertyValueRow",
    "get_entry",
    "list_entries",
    "load_bindings",
    "load_designation_by_id",
    "load_designations",
    "load_designations_for_write",
    "load_property_values",
]

#: The one status filter every public read applies - see the module
#: docstring. A tuple rather than a set so the SQL parameter order is
#: stable, and referenced by `backend/tests/test_api_public_status_filter.py`
#: rather than re-listed there.
PUBLIC_STATUSES: Final[tuple[str, ...]] = (CatalogueEntryStatus.ACTIVE.value,)


@dataclass(frozen=True, slots=True)
class EntryPage:
    """One page of entries plus the cursor for the next, if any.

    `next_cursor` is `None` exactly when this is the last page - decided by
    the one extra row `list_entries` asked for, never by a `COUNT(*)`.
    """

    entries: tuple[CatalogueEntry, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DesignationRow:
    """A catalogue-authored synonym or non-en-AU preferred variant (ADR-0022
    - the catalogue's own en-AU preferred term is never a `designation` row,
    it is `catalogue_entry.preferred_term`).

    `id` is this row's own primary key - an internal detail, never put on
    the public `Designation` response model. It exists on this row type
    only so a write route can re-read the exact row it just wrote
    (issue #224, matching `BindingRow.id`'s own rationale):
    `(entry_id, term_key, language)` is unique only among *active*
    designations, so a term added, retired, and re-added leaves two
    retired rows sharing a `term_key`, and only `id` still tells them
    apart."""

    id: uuid.UUID
    entry_id: uuid.UUID
    term: str
    use: str
    language: str
    status: str
    length: int


@dataclass(frozen=True, slots=True)
class BindingRow:
    """A code binding, with `replaced_by_code` already resolved.

    `code` is a `str` and stays one end to end (FR-06). `replaced_by_code`
    is the successor binding's *code*, resolved by a self-join in
    `load_bindings` - the module docstring's rule two: the UUID
    `code_binding.replaced_by_binding_id` actually holds never reaches a
    caller of this module at all.

    `id` is this row's own primary key - an internal detail, never put on
    the public `Binding` response model (see `catalogue_shared.py`'s own
    rule). It exists on this row type only so a write route can re-read the
    exact row it just wrote: `(entry_id, code)` is unique only among
    *active* bindings, so a code bound, retired, and bound again leaves two
    retired rows sharing a code, and only `id` still tells them apart
    (issue #219 review).
    """

    id: uuid.UUID
    entry_id: uuid.UUID
    system: str
    code: str
    fsn: str
    au_preferred_term: str | None
    edition_hint: str
    status: str
    retirement_reason: str | None
    replaced_by_code: str | None


@dataclass(frozen=True, slots=True)
class PropertyValueRow:
    """One property value, joined to its definition.

    `value` is the raw JSONB as stored; rendering it is the datatype
    handler's job, not this module's (FR-77/ADR-0013), so `datatype` is
    carried through for the caller to resolve a handler with. There is
    deliberately no `switch` on it here.
    """

    entry_id: uuid.UUID
    property_key: str
    label: str
    datatype: str
    cardinality: str
    ordinal: int
    value: object
    justification: str | None


def list_entries(session: Session, *, limit: int, after: str | None = None) -> EntryPage:
    """One keyset page of active entries, ordered by `business_key`.

    `after` is the last `business_key` of the previous page (exclusive).
    Because `business_key` is `UNIQUE` and the sort is on it alone, the
    ordering is total: no two rows can tie, so no row can be skipped or
    served twice across a page boundary - which is the failure `OFFSET`
    exhibits the moment a concurrent insert lands mid-scan.
    """
    statement = (
        select(CatalogueEntry)
        .where(CatalogueEntry.status.in_(PUBLIC_STATUSES))
        .order_by(CatalogueEntry.business_key)
        # One more row than asked for: its existence *is* the answer to
        # "is there a next page", at the cost of one extra row rather than
        # a second query.
        .limit(limit + 1)
    )
    if after is not None:
        statement = statement.where(CatalogueEntry.business_key > after)

    rows = tuple(session.execute(statement).scalars().all())
    if len(rows) > limit:
        page = rows[:limit]
        return EntryPage(entries=page, next_cursor=page[-1].business_key)
    return EntryPage(entries=rows, next_cursor=None)


def get_entry(session: Session, business_key: str) -> CatalogueEntry:
    """One active entry, or `EntryNotFoundError`.

    A non-`active` entry raises exactly the same error as a `business_key`
    that was never minted, and that is deliberate: a distinguishable
    response (a 403, or a 404 with a different detail) would confirm the key
    exists, which is a disclosure about unpublished editorial work
    (`draft`) that the public surface has no business making. The caller
    cannot tell the two apart, and neither can anyone enumerating keys.
    """
    entry = session.execute(
        select(CatalogueEntry)
        .where(CatalogueEntry.business_key == business_key)
        .where(CatalogueEntry.status.in_(PUBLIC_STATUSES))
    ).scalar_one_or_none()
    if entry is None:
        raise EntryNotFoundError(
            f"no publicly visible catalogue_entry with business_key {business_key!r}"
        )
    return entry


def load_designations(
    session: Session, entry_ids: Iterable[uuid.UUID]
) -> tuple[DesignationRow, ...]:
    """Every *active* designation for the given entries, in a stable order.

    Retired designations are omitted: unlike a retired code binding (which
    FR-08 requires be published, so an implementer can follow the
    supersession chain), a retired synonym carries no forward pointer and no
    obligation - it is editorial history, and `/catalogue/entries/{key}/
    designations` is a list of the terms an entry *is* known by.

    `(use, language, term)` rather than insertion order or `id`: an
    `ORDER BY` on a UUID primary key is a stable-looking accident, and an
    unordered response makes every whole-body comparison in a client's own
    test suite flap.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    rows = session.execute(
        select(Designation)
        .where(Designation.entry_id.in_(ids))
        .where(Designation.status == DesignationStatus.ACTIVE.value)
        .order_by(Designation.use, Designation.language, Designation.term)
    ).scalars()
    return tuple(
        DesignationRow(
            id=row.id,
            entry_id=row.entry_id,
            term=row.term,
            use=row.use,
            language=row.language,
            status=row.status,
            length=row.length,
        )
        for row in rows
    )


def load_designations_for_write(
    session: Session, entry_ids: Iterable[uuid.UUID]
) -> tuple[DesignationRow, ...]:
    """Every designation for the given entries, active *and* retired
    (issue #224) - unlike `load_designations` above, which is the FR-20
    public read surface and omits retired rows on purpose (see its own
    docstring).

    An admin write route needs the retired case too: re-reading the exact
    row a retirement or amendment just wrote (by `id`, matching
    `nptc.catalogue.bindings`'s own `_row_to_binding` precedent) has to
    find it whether it ended up active or retired. Not exposed to the
    public API - callers outside `nptc.api.routers.catalogue_designations`
    should not have a reason to see a retired designation."""
    ids = tuple(entry_ids)
    if not ids:
        return ()
    rows = session.execute(
        select(Designation)
        .where(Designation.entry_id.in_(ids))
        .order_by(Designation.use, Designation.language, Designation.term)
    ).scalars()
    return tuple(
        DesignationRow(
            id=row.id,
            entry_id=row.entry_id,
            term=row.term,
            use=row.use,
            language=row.language,
            status=row.status,
            length=row.length,
        )
        for row in rows
    )


def load_designation_by_id(session: Session, designation_id: uuid.UUID) -> DesignationRow | None:
    """The one designation with this primary key, active or retired, or
    `None` - a point lookup for a write route re-reading the exact row it
    just amended or retired (issue #224 review finding 3), rather than
    `load_designations_for_write` reloading and filtering *every*
    designation on the entry (unbounded for an entry with a long retired
    history) to find the one row by `id`."""
    row = session.get(Designation, designation_id)
    if row is None:
        return None
    return DesignationRow(
        id=row.id,
        entry_id=row.entry_id,
        term=row.term,
        use=row.use,
        language=row.language,
        status=row.status,
        length=row.length,
    )


def load_bindings(session: Session, entry_ids: Iterable[uuid.UUID]) -> tuple[BindingRow, ...]:
    """Every binding for the given entries - active *and* retired (FR-08).

    A retired binding is published on purpose: an implementer holding a code
    that has since been inactivated needs to learn that from this API, along
    with `retirement_reason` and, where PRD FR-08's replacement case
    applies, the code that superseded it. Omitting retired bindings would
    leave them silently discovering the change as a lookup that stopped
    matching.

    The successor is reached by an `OUTER JOIN` back onto `code_binding` and
    projected as its `code`, never its id - see the module docstring's rule
    two. `LEFT`, not inner: `replaced_by_binding_id` is `NULL` for both
    every active binding and every retirement with no successor, and an
    inner join would silently drop exactly those rows.

    `(status, code)` order puts `active` before `retired` (alphabetically,
    which happens to be the order a reader wants) and is total within an
    entry, since at most one binding per entry is active and `code` is
    unique among the rest.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    successor = aliased(CodeBinding)
    rows = session.execute(
        select(
            CodeBinding.id,
            CodeBinding.entry_id,
            CodeBinding.system,
            CodeBinding.code,
            CodeBinding.fsn,
            CodeBinding.au_preferred_term,
            CodeBinding.edition_hint,
            CodeBinding.status,
            CodeBinding.retirement_reason,
            successor.code.label("replaced_by_code"),
        )
        .outerjoin(successor, CodeBinding.replaced_by_binding_id == successor.id)
        .where(CodeBinding.entry_id.in_(ids))
        .order_by(CodeBinding.status, CodeBinding.code)
    ).all()
    return tuple(
        BindingRow(
            id=row.id,
            entry_id=row.entry_id,
            system=row.system,
            code=row.code,
            fsn=row.fsn,
            au_preferred_term=row.au_preferred_term,
            edition_hint=row.edition_hint,
            status=row.status,
            retirement_reason=row.retirement_reason,
            replaced_by_code=row.replaced_by_code,
        )
        for row in rows
    )


def load_property_values(
    session: Session, entry_ids: Iterable[uuid.UUID]
) -> tuple[PropertyValueRow, ...]:
    """Every property value for the given entries, joined to its definition.

    One statement, not "load the values, then load the definitions they
    reference": `property_value.property_key` is a foreign key onto
    `property_definition.key` (ADR-0012 chose the natural key precisely so a
    join like this needs no surrogate lookup), so the definition's `label`,
    `datatype` and `cardinality` come back on the same row. A separate
    definition load would be a second query answering a question this join
    has already answered.

    A value whose definition is `deprecated` is still served. FR-11/FR-12
    make a definition undeletable and its key immutable, so a deprecated
    definition still describes stored values accurately - suppressing them
    would silently drop published data from an entry the moment an
    administrator deprecated a property, which is a bigger surprise than
    serving a value whose definition is no longer offered for new entries.

    `(property_key, ordinal)` order: `ordinal` is meaningful within a
    multi-valued property (`nptc.db.models.property_value`: zero-based, and
    the order the values are in), so sorting by it is not cosmetic.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    rows = session.execute(
        select(
            PropertyValue.entry_id,
            PropertyValue.property_key,
            PropertyValue.ordinal,
            PropertyValue.value,
            PropertyValue.justification,
            PropertyDefinition.label,
            PropertyDefinition.datatype,
            PropertyDefinition.cardinality,
        )
        .join(PropertyDefinition, PropertyValue.property_key == PropertyDefinition.key)
        .where(PropertyValue.entry_id.in_(ids))
        .order_by(PropertyValue.property_key, PropertyValue.ordinal)
    ).all()
    return tuple(
        PropertyValueRow(
            entry_id=row.entry_id,
            property_key=row.property_key,
            label=row.label,
            datatype=row.datatype,
            cardinality=row.cardinality,
            ordinal=row.ordinal,
            value=row.value,
            justification=row.justification,
        )
        for row in rows
    )
