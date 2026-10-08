"""The public catalogue's read layer (FR-20).

Everything the FR-20 API serves is read through this module. It is separate
from `entries.py`/`designations.py`/`bindings.py`, which are write paths with
domain rules attached. A read has exactly two rules of its own.

**Rule one: `PUBLIC_STATUSES` is the only status filter, and it is one
tuple.** `active` and nothing else - not `draft`, `withdrawn` or
`deprecated`. A deprecated entry is absent rather than served with a flag:
the surface is what a vendor builds a request form from, and a deprecated
entry is one they must stop offering. Every query imports the same constant,
so a query without the filter is a visible omission, and
`backend/tests/test_api_public_status_filter.py` asserts the absence over
every endpoint.

**Rule two: an internal UUID never leaves this module.** `catalogue_entry.id`
batches the child loads and does nothing else, and
`code_binding.replaced_by_binding_id` is resolved to the successor's *code*
by a self-join (`load_bindings`). PRD SS6.2 makes `business_key` the only
public identifier; `nptc.auth.identity.UserRef` is the same boundary for
`app_user`.

**No `relationship()`.** None exists in `nptc.db.models`; every association
is an explicit `select()`. Each loader takes a collection of entry ids and
filters `.in_(...)`, so a list endpoint issues a fixed number of queries
whatever the page size.

Keyset pagination, never `OFFSET` (ADR-0024). `list_entries` asks for one row
more than the caller wanted, and that row decides whether a next page exists,
so no endpoint here runs a page-total `COUNT(*)`.

**The one exception to that `COUNT` ban, and why it is not one** (FR-16,
ADR-0032). `nptc.catalogue.facets.compute_facets` does count, and this
paragraph exists so that reads as a considered exception, not an oversight.
The ban is on a *page total*: a number the client did not ask for, that costs
a scan of everything the page did not serve, and that ADR-0024 does without
because keyset paging has no use for it. A facet count is the opposite on
every point. It is the answer to the question: a facet with no count is a list
of words, not a filter, and FR-16 asks for counts by name. It is bounded, to
`FACET_BUCKET_CAP` buckets per facet. And it reads one property's rows through
that property's index rather than the whole table; `test_db_property_index_plan.py`
`EXPLAIN`s the plan, and ADR-0032 records which parts of the query the index
serves. `list_entries` below runs no count of any kind: it accepts filters and
returns no facets.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased

from nptc.catalogue.errors import CodeLookupNotFoundError, EntryNotFoundError
from nptc.catalogue.facets import FilterSelection, filter_predicates
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM, CodeBinding, CodeBindingStatus
from nptc.db.models.designation import Designation, DesignationStatus
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.validation_finding import ValidationFinding, ValidationFindingStatus

__all__ = [
    "PUBLIC_STATUSES",
    "BindingRow",
    "DesignationRow",
    "EntryPage",
    "PropertyValueRow",
    "RowFacts",
    "get_entry",
    "get_entry_by_code",
    "list_entries",
    "load_bindings",
    "load_designation_by_id",
    "load_designations",
    "load_designations_any_status",
    "load_property_values",
    "open_finding_business_keys",
    "row_facts",
    "row_facts_for",
]

#: The one status filter every public read applies (module docstring). A tuple
#: rather than a set, so the SQL parameter order is stable.
PUBLIC_STATUSES: Final[tuple[str, ...]] = (CatalogueEntryStatus.ACTIVE.value,)

#: The system properties `nptc.db.bootstrap` seeds, which `row_facts` shows on every row.
DISCIPLINE_PROPERTY_KEY: Final = "discipline"
SPECIMEN_PROPERTY_KEY: Final = "specimen"


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
    """A catalogue-authored synonym (ADR-0022: the catalogue's own preferred
    term is never a `designation` row, it is `catalogue_entry.preferred_term`).

    `id` is this row's own primary key, never put on the public `Designation`
    response model. It lets a write route re-read the exact row it wrote:
    `(entry_id, term_key)` is unique only among *active*
    designations, so a term added, retired and re-added leaves two retired
    rows sharing a `term_key`, and only `id` tells them apart."""

    id: uuid.UUID
    entry_id: uuid.UUID
    term: str
    status: str
    length: int


@dataclass(frozen=True, slots=True)
class BindingRow:
    """A code binding, with `replaced_by_code` already resolved.

    `code` is a `str` end to end (FR-06). `replaced_by_code` is the successor
    binding's *code*, resolved by a self-join in `load_bindings` (module
    docstring, rule two).

    `id` is this row's own primary key, never put on the public `Binding`
    response model (see `catalogue_shared.py`). It lets a write route re-read
    the exact row it wrote: `(entry_id, code)` is unique only among *active*
    bindings, so a code bound, retired and bound again leaves two retired
    rows sharing a code, and only `id` tells them apart.
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

    `value` is the raw JSONB as stored. Rendering it is the datatype
    handler's job (FR-77, ADR-0013), so `datatype` is carried through for the
    caller to resolve a handler with; there is no `switch` on it here.

    `status` is the *definition's* status, not the value's: `property_value`
    has no status column. Without it a client cannot tell a deprecated
    property's values from an active one's except through a second call to
    `GET /registry/properties?include_deprecated=true`.
    """

    entry_id: uuid.UUID
    property_key: str
    label: str
    datatype: str
    cardinality: str
    status: str
    ordinal: int
    value: object
    justification: str | None


def list_entries(
    session: Session,
    *,
    limit: int,
    after: str | None = None,
    filters: Sequence[FilterSelection] = (),
) -> EntryPage:
    """One keyset page of active entries, ordered by `business_key`.

    `after` is the last `business_key` of the previous page (exclusive).
    `business_key` is `UNIQUE` and the only sort column, so the order is
    total: no row is skipped or served twice across a page boundary, which is
    what `OFFSET` gets wrong when a concurrent insert lands mid-scan.

    `filters` are FR-16's facet selections, built by `nptc.catalogue.facets`
    from the descriptors `/catalogue/search` uses and applied directly to
    `catalogue_entry`, since a browse has no scored CTE.

    **The cursor is unaffected by the filter set, unlike the search one.** A
    search cursor carries a relevance score, which means nothing under a
    changed filter set. This cursor is a `business_key` in a total order
    whatever the filters, so replaying it under another filter set names an
    unambiguous position and returns the differently filtered page the
    client asked for.
    """
    statement = (
        select(CatalogueEntry)
        .where(CatalogueEntry.status.in_(PUBLIC_STATUSES))
        .where(*filter_predicates(filters))
        .order_by(CatalogueEntry.business_key)
        # One extra row decides whether a next page exists.
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

    A non-`active` entry raises the same error as a `business_key` that was
    never minted. A distinguishable response would confirm the key exists,
    which discloses unpublished editorial work (`draft`) to anyone
    enumerating keys.
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


def _get_entry_by_code_statement(system: str, code: str) -> Select[CatalogueEntry]:
    """The statement `get_entry_by_code` runs, factored out so
    `test_db_code_binding_index_plan.py` can `EXPLAIN` the exact query.

    `system` and `code` are bound parameters: SQLAlchemy Core binds every
    `==` comparison, so no caller text is concatenated into SQL (NFR-22).
    """
    return (
        select(CatalogueEntry)
        .join(CodeBinding, CodeBinding.entry_id == CatalogueEntry.id)
        .where(CodeBinding.system == system)
        .where(CodeBinding.code == code)
        .where(CatalogueEntry.status.in_(PUBLIC_STATUSES))
        .order_by(
            (CodeBinding.status == CodeBindingStatus.ACTIVE.value).desc(),
            CodeBinding.retired_at.desc(),
            CatalogueEntry.business_key.asc(),
        )
        .limit(1)
    )


def get_entry_by_code(session: Session, system: str, code: str) -> CatalogueEntry:
    """One entry resolved by an exact code, active binding preferred (FR-17).

    At most one *active* binding matches `(system, code)`
    (`ix_code_binding_one_active_entry_per_code` is a database invariant), so
    when one does, its entry is returned. Otherwise a retired binding may
    match: FR-08 keeps a retired binding resolvable, and more than one entry
    can hold the same code as a retired binding. `ORDER BY ... LIMIT 1` picks
    the winner in one statement: active first, then the most recently retired
    (`retired_at DESC`), then `business_key ASC` for a total order
    (ADR-0033).

    `PUBLIC_STATUSES` applies as in every query here, so a code bound only to
    a hidden entry raises the same `CodeLookupNotFoundError` as a code nobody
    has bound (rule one).

    `ix_code_binding_system_code` is non-partial, unlike every other index on
    this table. The `WHERE` clause has no `status` predicate, because it must
    see retired rows, so the planner cannot prove any of the partial
    `WHERE status = 'active'` indexes applicable.
    `test_db_code_binding_index_plan.py` `EXPLAIN`s this exact statement.
    """
    statement = _get_entry_by_code_statement(system, code)
    entry = session.execute(statement).scalars().first()
    if entry is None:
        raise CodeLookupNotFoundError(
            f"no publicly visible catalogue_entry with a binding for "
            f"system={system!r} code={code!r}"
        )
    return entry


def load_designations(
    session: Session, entry_ids: Iterable[uuid.UUID]
) -> tuple[DesignationRow, ...]:
    """Every *active* designation for the given entries, in a stable order.

    Retired designations are omitted. A retired code binding is published
    because FR-08 requires an implementer to be able to follow the
    supersession chain; a retired synonym carries no forward pointer and no
    obligation, so it is editorial history. That reasoning is about the
    *public* surface. The admin read calls `load_designations_any_status`,
    for an editor to whom that history matters.

    `term` order, not insertion order or the UUID `id`,
    which is stable only by accident: an unordered response makes a client's
    whole-body comparisons flap.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    rows = session.execute(
        select(Designation)
        .where(Designation.entry_id.in_(ids))
        .where(Designation.status == DesignationStatus.ACTIVE.value)
        .order_by(Designation.term)
    ).scalars()
    return tuple(
        DesignationRow(
            id=row.id,
            entry_id=row.entry_id,
            term=row.term,
            status=row.status,
            length=row.length,
        )
        for row in rows
    )


def load_designations_any_status(
    session: Session, entry_ids: Iterable[uuid.UUID]
) -> tuple[DesignationRow, ...]:
    """Every designation for the given entries, active *and* retired - unlike
    `load_designations`, the public FR-20 read, which omits retired rows.

    Three callers want the unfiltered set:

    - the admin entry read, which puts these rows on the wire for an editor
      for whom that history is what is being decided;
    - `nptc.api.routers.catalogue_designations`'s write routes, which re-read
      the exact row a retirement or amendment wrote (by `id`), whether it
      ended up active or retired;
    - `nptc.catalogue.history.load_history`, which consumes only `.id`, to
      resolve which `audit_event` rows belong to the entry's designations
      (FR-19): a retired designation's history belongs in the entry's too.

    `(status, term, id)` order. `load_designations`'s `term` order is total
    only among *active* rows, because `ix_designation_no_duplicate_active_term`
    allows one active row per comparison key. A term added, retired, re-added
    and retired again leaves two rows with an identical term, which Postgres
    could return in either order between calls. `status`
    comes first, as in `load_bindings`, so `active` precedes `retired` and
    matches `sortedTermRows` in `designations-panel.tsx`. `id` is the final
    tie-break, as in `load_bindings`.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    rows = session.execute(
        select(Designation)
        .where(Designation.entry_id.in_(ids))
        .order_by(Designation.status, Designation.term, Designation.id)
    ).scalars()
    return tuple(
        DesignationRow(
            id=row.id,
            entry_id=row.entry_id,
            term=row.term,
            status=row.status,
            length=row.length,
        )
        for row in rows
    )


def load_designation_by_id(session: Session, designation_id: uuid.UUID) -> DesignationRow | None:
    """The one designation with this primary key, active or retired, or
    `None`: a point lookup for a write route re-reading the row it just
    amended or retired. `load_designations_any_status` would load and filter
    every designation on the entry, unbounded for a long retired history."""
    row = session.get(Designation, designation_id)
    if row is None:
        return None
    return DesignationRow(
        id=row.id,
        entry_id=row.entry_id,
        term=row.term,
        status=row.status,
        length=row.length,
    )


def load_bindings(session: Session, entry_ids: Iterable[uuid.UUID]) -> tuple[BindingRow, ...]:
    """Every binding for the given entries - active *and* retired (FR-08).

    A retired binding is published on purpose: an implementer holding a code
    that has since been inactivated learns that from this API, with
    `retirement_reason` and, in FR-08's replacement case, the code that
    superseded it. Omitting it would leave them to discover the change as a
    lookup that stopped matching.

    The successor is reached by an `OUTER JOIN` back onto `code_binding` and
    projected as its `code`, never its id (module docstring, rule two).
    `LEFT`, not inner: `replaced_by_binding_id` is `NULL` for every active
    binding and every retirement without a successor, and an inner join would
    drop those rows.

    `(status, code, id)` order puts `active` before `retired`. `(status,
    code)` is total only among an entry's *active* bindings, since at most
    one is active. The same code can be bound, retired, bound again and
    retired again, so two retired rows can share a `code`; `id` is the
    tiebreaker nothing else can supply.
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
        .order_by(CodeBinding.status, CodeBinding.code, CodeBinding.id)
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
    session: Session,
    entry_ids: Iterable[uuid.UUID],
    *,
    property_keys: Iterable[str] | None = None,
) -> tuple[PropertyValueRow, ...]:
    """Every property value for the given entries, joined to its definition.

    One statement: `property_value.property_key` is a foreign key onto
    `property_definition.key` (ADR-0012 chose the natural key so a join like
    this needs no surrogate lookup), so `label`, `datatype` and `cardinality`
    arrive on the same row.

    A value whose definition is `deprecated` is still served. FR-11/FR-12
    make a definition undeletable and its key immutable, so it still
    describes the stored values accurately. Suppressing them would drop
    published data the moment an administrator deprecated a property.

    `(property_key, ordinal)` order: `ordinal` is the zero-based position
    within a multi-valued property (`nptc.db.models.property_value`), so
    sorting by it is not cosmetic.

    `property_keys`, when given, narrows the result to those properties. The
    entry-detail assembly leaves it `None`; the single-property write route
    passes its one key, so the work scales with that write, not with how many
    properties the entry carries.
    """
    ids = tuple(entry_ids)
    if not ids:
        return ()
    stmt = (
        select(
            PropertyValue.entry_id,
            PropertyValue.property_key,
            PropertyValue.ordinal,
            PropertyValue.value,
            PropertyValue.justification,
            PropertyDefinition.label,
            PropertyDefinition.datatype,
            PropertyDefinition.cardinality,
            PropertyDefinition.status,
        )
        .join(PropertyDefinition, PropertyValue.property_key == PropertyDefinition.key)
        .where(PropertyValue.entry_id.in_(ids))
        .order_by(PropertyValue.property_key, PropertyValue.ordinal)
    )
    if property_keys is not None:
        stmt = stmt.where(PropertyValue.property_key.in_(tuple(property_keys)))
    rows = session.execute(stmt).all()
    return tuple(
        PropertyValueRow(
            entry_id=row.entry_id,
            property_key=row.property_key,
            label=row.label,
            datatype=row.datatype,
            cardinality=row.cardinality,
            status=row.status,
            ordinal=row.ordinal,
            value=row.value,
            justification=row.justification,
        )
        for row in rows
    )


def open_finding_business_keys(session: Session, business_keys: Iterable[str]) -> frozenset[str]:
    """Which of these business keys name an entry carrying at least one
    `open` `ValidationFinding` (FR-18) - one batch lookup per collection or
    detail response, not a per-row subquery.

    Keyed on `business_key`, not `entry_id` as the `load_*` loaders are:
    `nptc.catalogue.search.SearchHit` carries no entry id (rule two), so an
    id-keyed lookup could not serve search results.
    """
    keys = tuple(business_keys)
    if not keys:
        return frozenset()
    rows = (
        session.execute(
            select(CatalogueEntry.business_key)
            .join(ValidationFinding, ValidationFinding.entry_id == CatalogueEntry.id)
            .where(CatalogueEntry.business_key.in_(keys))
            .where(ValidationFinding.status == ValidationFindingStatus.OPEN.value)
            .distinct()
        )
        .scalars()
        .all()
    )
    return frozenset(rows)


@dataclass(frozen=True, slots=True)
class RowFacts:
    """What a list or search row shows beyond the entry's own columns.

    `code` and `fsn` come from the entry's one active SNOMED CT binding, or are
    `None`. A retired binding never appears here: a row names the code to use
    now, and the detail's `bindings` carries the history. `fsn` is as stored
    (FR-82), semantic tag intact. `disciplines` and `specimens` are stored
    labels, also as stored.
    """

    has_open_finding: bool
    code: str | None
    fsn: str | None
    disciplines: tuple[str, ...]
    specimens: tuple[str, ...]


def row_facts(session: Session, business_keys: Iterable[str]) -> dict[str, RowFacts]:
    """`RowFacts` for each business key, in three statements whatever the page
    size. Every key given is a key of the result.

    A discipline or specimen is its stored `display`, falling back to the code
    where the value carries none - the same generic JSON handling as a facet
    bucket's label (`nptc.catalogue.facets`), and for the same reason: no
    terminology call on a read path (FR-54).
    """
    keys = tuple(dict.fromkeys(business_keys))
    if not keys:
        return {}
    open_findings = open_finding_business_keys(session, keys)
    bindings = {
        business_key: (code, fsn)
        for business_key, code, fsn in session.execute(
            select(CatalogueEntry.business_key, CodeBinding.code, CodeBinding.fsn)
            .join(CodeBinding, CodeBinding.entry_id == CatalogueEntry.id)
            .where(CatalogueEntry.business_key.in_(keys))
            .where(CodeBinding.status == CodeBindingStatus.ACTIVE.value)
            .where(CodeBinding.system == SNOMED_CT_SYSTEM)
        ).all()
    }
    labels: dict[str, dict[str, list[str]]] = {}
    for business_key, property_key, label in session.execute(
        select(
            CatalogueEntry.business_key,
            PropertyValue.property_key,
            func.coalesce(
                func.jsonb_extract_path_text(PropertyValue.value, "display"),
                func.jsonb_extract_path_text(PropertyValue.value, "code"),
            ),
        )
        .join(PropertyValue, PropertyValue.entry_id == CatalogueEntry.id)
        .where(CatalogueEntry.business_key.in_(keys))
        .where(PropertyValue.property_key.in_((DISCIPLINE_PROPERTY_KEY, SPECIMEN_PROPERTY_KEY)))
        .order_by(CatalogueEntry.business_key, PropertyValue.property_key, PropertyValue.ordinal)
    ):
        if label is not None:
            labels.setdefault(business_key, {}).setdefault(property_key, []).append(label)
    facts: dict[str, RowFacts] = {}
    for key in keys:
        code, fsn = bindings.get(key, (None, None))
        own = labels.get(key, {})
        facts[key] = RowFacts(
            has_open_finding=key in open_findings,
            code=code,
            fsn=fsn,
            disciplines=tuple(own.get(DISCIPLINE_PROPERTY_KEY, ())),
            specimens=tuple(own.get(SPECIMEN_PROPERTY_KEY, ())),
        )
    return facts


def row_facts_for(session: Session, business_key: str) -> RowFacts:
    """The single-entry case of `row_facts`, for the detail routes."""
    return row_facts(session, (business_key,))[business_key]
