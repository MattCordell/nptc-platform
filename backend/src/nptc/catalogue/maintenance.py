"""The all-status catalogue listing for the maintenance surface (issue #266,
FR-14, FR-15, FR-16, FR-36, FR-44), and its server-side sort (issue #287,
FR-16's second acceptance criterion).

`nptc.catalogue.queries`' own module docstring makes `PUBLIC_STATUSES` "the
only status filter" that module applies - so a `statuses=` parameter does
not belong on `queries.list_entries`, and this module exists to hold the
one query that needs a different scope instead of loosening that rule.

**Why every status, not "not hidden".** `PUBLIC_STATUSES` names what a
vendor may see; this module names what an administrator holding
`Permission.CATALOGUE_EDIT_PUBLISHED` may see, which is every entry that
exists, whatever its `CatalogueEntryStatus` - the whole reason this issue
exists is that `draft`, `deprecated` and `withdrawn` entries were otherwise
unreachable to work on. `MAINTENANCE_STATUSES` is therefore derived from the
enum itself rather than hand-listed, so a fifth status is covered on the day
it is added rather than the day someone remembers to widen a tuple here.

**Sort (issue #287).** `GET /catalogue/admin/entries` orders by
`business_key` alone before this issue - FR-16's second acceptance
criterion asks that filter *and sort* state survive a reload and a pasted
link, and there was no `sort` parameter to put in that URL. Four columns
are offered (`SortName`): `business_key` (the pre-existing default),
`preferred_term`, `updated_at`, `status`. Only `business_key` is unique, so
paging by any other column needs a composite keyset over `(sort_value,
business_key)` rather than `business_key` alone - `business_key` remains the
tie-break in every case, exactly as it already was the *only* ordering
column before this issue.

**One cursor grammar for every sort, including the default.** The cursor is
`"<sort value>:<digest>:<business key>"` for all four sorts - the
`business_key` sort's own cursor is the degenerate case where the sort value
and the business key are the same string, not a second, bare-`business_key`
shape. Retiring that old shape (rather than keeping two cursor formats) costs
nothing: this is pre-alpha with no compatibility obligation, and one shape
used uniformly is less code and less test surface than a shape that varies
by sort. `_parse_cursor` and `_format_cursor` mirror `nptc.catalogue.search`'s
own `_parse_cursor`/`_format_cursor` shape, including the reason the split is
done with `rpartition` from the *right*, twice: the business key and the
digest are both fixed, `:`-free shapes (`BUSINESS_KEY_PATTERN` admits no
colon, and a digest is hex), but an ISO-8601 `updated_at` sort value itself
contains colons - splitting from the left would break on it.

**The digest binds to the sort column and the filter set, not to status
scope.** Sort-column binding is this issue's own acceptance criterion:
replaying a cursor minted under one sort against a request naming another
must be refused, because `sort_value > :after` means something different
under a different ordering. The filter set is bound for the same reason
`nptc.catalogue.search._request_digest` binds it - one field appended to the
digest keeps "what does this cursor refuse" answerable with one sentence for
both routes - even though, unlike a relevance score, a listing's sort value
is an intrinsic row property and filtering narrows candidates without
changing what "greater than" means, so filter binding is not needed for
keyset *correctness* here the way sort binding is. Status scope is not
bound: there is only one scope on this route today (`MAINTENANCE_STATUSES`),
and binding a cursor to a constant is exactly the speculative infrastructure
CLAUDE.md says not to add.

**No new database indexes.** The catalogue is about 2,000 rows today with a
ceiling around 5,000 for this domain (see `docs/adr/
0024-catalogue-search-and-pagination.md`'s amendments) - a sequential scan
plus in-memory sort at that size has no plausible pathology a composite
index would fix, and `status` in particular has only four distinct values,
low enough that the planner would likely ignore an index on it regardless.
`backend/tests/test_db_search_index.py` (or its sibling covering this
statement) `EXPLAIN`s the real statement at current table size as evidence
for this deferral, rather than leaving it an unverified assumption.

**The substrate was already built for this.** Both `catalogue_entry`
trigram/full-text indexes (`nptc.db.models.catalogue_entry`) are
deliberately non-partial on `status`, and `nptc.catalogue.search`'s entry
status filter is bound as `:statuses` rather than written as the literal
`'active'` the way the `designation`/`code_binding` branches are - both
recorded in their own modules as being for this issue's benefit. This
module is the query surface that finally uses them; `nptc.catalogue.search`
threads the same `statuses` argument through for the search half.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final, Literal
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session

from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.facets import FilterSelection, filter_digest_material, filter_predicates
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus

__all__ = [
    "MAINTENANCE_STATUSES",
    "ListingCursorMismatchError",
    "ListingPage",
    "ListingRow",
    "MalformedListingCursorError",
    "SortName",
    "build_listing_statement",
    "list_entries_any_status",
]

#: Every status a `catalogue_entry` row may hold, derived from the enum
#: rather than hand-listed - see the module docstring.
MAINTENANCE_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in CatalogueEntryStatus
)

#: The administrator-meaningful orderings `GET /catalogue/admin/entries`
#: accepts (issue #287). `Literal`, not a hand-validated free string, so
#: FastAPI puts the enum in `docs/api/openapi.json` and 422s an unrecognised
#: value before any handler code runs - see `catalogue_admin.py`'s own
#: `sort` query parameter.
SortName = Literal["business_key", "preferred_term", "updated_at", "status"]

#: Every `SortName`'s comparison column. `preferred_term` sorts by
#: `preferred_term_key` (FR-05's normalised comparison form), not the raw
#: display column - the same column `nptc.catalogue.collisions` already
#: indexes and compares on, so this reuses an existing index rather than
#: sorting on an unindexed expression. See the module docstring's open
#: question about the resulting display-order mismatch for punctuation- or
#: case-only differences.
_SORT_COLUMNS: Final[dict[SortName, ColumnElement[Any]]] = {
    "business_key": type_cast("ColumnElement[Any]", CatalogueEntry.business_key),
    "preferred_term": type_cast("ColumnElement[Any]", CatalogueEntry.preferred_term_key),
    "updated_at": type_cast("ColumnElement[Any]", CatalogueEntry.updated_at),
    "status": type_cast("ColumnElement[Any]", CatalogueEntry.status),
}

#: See `nptc.catalogue.search._CURSOR_SEPARATOR` - identical reasoning here:
#: none of a sort value, a hex digest, or a `business_key` can itself contain
#: `:`, so the split is unambiguous. A sort value that is an ISO-8601
#: timestamp *does* contain `:` internally, which is exactly why parsing
#: below splits from the right rather than the left.
_CURSOR_SEPARATOR: Final[str] = ":"

#: Matching `nptc.catalogue.search._CURSOR_QUERY_DIGEST_BYTES` - 8 bytes of
#: BLAKE2s, deliberately unkeyed for the identical reason: a listing cursor
#: is not a capability, so there is nothing here to authenticate, only a
#: client-bug replay to detect.
_CURSOR_DIGEST_BYTES: Final[int] = 8


class MalformedListingCursorError(ValueError):
    """Raised for an `after` cursor this module did not mint - the listing
    counterpart of `nptc.catalogue.search.MalformedSearchCursorError`, and
    for the same reason: silently falling back to page one would make a
    client's paging loop restart forever, which looks like a slow catalogue
    rather than an error."""

    http_status: ClassVar[int] = 422


class ListingCursorMismatchError(MalformedListingCursorError):
    """Raised for a well-formed cursor minted for a *different* sort or
    filter set - the listing counterpart of `nptc.catalogue.search.
    SearchCursorQueryMismatchError`. A subclass, so `nptc.api.errors` maps it
    to the same 422 and the same client-facing sentence without a second
    handler; the distinction is only useful in the log.

    Refused rather than served: a keyset predicate over one sort's values
    means nothing replayed against another sort's ordering, and would
    silently select a window that is neither surface's next page."""


@dataclass(frozen=True, slots=True)
class ListingRow:
    """One entry, exactly the fields `catalogue_admin.py`'s listing route
    puts on the wire. A plain row, not the mapped `CatalogueEntry`, matching
    `nptc.catalogue.search.SearchHit`'s own precedent - the composed
    statement below selects columns explicitly (it has to, to also carry the
    per-sort `sort_value` used for paging), so there is no ORM instance to
    hand back."""

    business_key: str
    preferred_term: str
    status: str
    specimen_unconstrained: bool
    updated_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class ListingPage:
    rows: tuple[ListingRow, ...]
    next_cursor: str | None


def _digest(sort: SortName, filters: Sequence[FilterSelection]) -> str:
    """The cursor's fingerprint of the sort column and the filter set - see
    the module docstring on why those two and not status scope."""
    material = f"{len(sort.encode())}:{sort}{filter_digest_material(filters)}"
    return hashlib.blake2s(material.encode("utf-8"), digest_size=_CURSOR_DIGEST_BYTES).hexdigest()


def _format_sort_value(sort: SortName, sort_value: Any) -> str:
    """The cursor's text form of one row's sort value - `isoformat()` for
    `updated_at` (matched by `_parse_sort_value`'s `fromisoformat`), the raw
    string for the other three, all of which are already `Text` columns."""
    if sort == "updated_at":
        assert isinstance(sort_value, datetime)
        return sort_value.isoformat()
    return str(sort_value)


def _parse_sort_value(sort: SortName, raw: str, *, cursor: str) -> Any:
    if sort != "updated_at":
        return raw
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise MalformedListingCursorError(
            f"listing cursor {cursor!r} does not begin with a well-formed timestamp"
        ) from None


def _format_cursor(
    sort_value: Any, business_key: str, *, sort: SortName, filters: Sequence[FilterSelection]
) -> str:
    return _CURSOR_SEPARATOR.join(
        (_format_sort_value(sort, sort_value), _digest(sort, filters), business_key)
    )


def _parse_cursor(
    cursor: str, *, sort: SortName, filters: Sequence[FilterSelection]
) -> tuple[Any, str]:
    """`"<sort value>:<digest>:<business key>"`, parsed from the *right*:
    the business key and the digest are both fixed, `:`-free shapes, but a
    `updated_at` sort value is an ISO-8601 timestamp and contains `:` itself
    - see the module docstring."""
    remainder, key_separator, business_key = cursor.rpartition(_CURSOR_SEPARATOR)
    sort_value_text, digest_separator, digest = remainder.rpartition(_CURSOR_SEPARATOR)
    if not key_separator or not digest_separator:
        raise MalformedListingCursorError(
            f"listing cursor {cursor!r} is not '<sort value>:<digest>:<business key>'"
        )
    if not BUSINESS_KEY_PATTERN.fullmatch(business_key):
        raise MalformedListingCursorError(
            f"listing cursor {cursor!r} does not end with a well-formed business key"
        )
    # `compare_digest` rather than `==` - matching `search._parse_cursor`'s
    # own choice not to invite a future "optimisation" into a prefix check.
    if not hmac.compare_digest(digest, _digest(sort, filters)):
        raise ListingCursorMismatchError(
            f"listing cursor {cursor!r} was issued for a different sort or filter set"
        )
    sort_value = _parse_sort_value(sort, sort_value_text, cursor=cursor)
    return sort_value, business_key


def build_listing_statement(
    *,
    sort: SortName = "business_key",
    filters: Sequence[FilterSelection] = (),
    after_sort_value: Any = None,
    after_key: str | None = None,
    limit: int,
) -> Select[Any]:
    """The composed statement `list_entries_any_status` runs.

    Public, matching `nptc.catalogue.search.build_search_statement`'s own
    reasoning: `backend/tests/test_db_search_index.py` (or its sibling)
    `EXPLAIN`s the statement this function actually builds, not a
    hand-copied approximation of it.

    `ORDER BY <sort column>, business_key` - `business_key` is always the
    tie-break, including when `sort` is itself `"business_key"` (where it is
    a harmless repeat of the same column). The keyset predicate is the
    composite `(sort_value, business_key) > (after_sort_value, after_key)`
    every non-unique sort column needs; for `sort="business_key"`,
    `after_sort_value == after_key` always, so the predicate reduces to
    exactly `queries.list_entries`'s own `business_key > after` (the second
    disjunct can never hold when the first does not).
    """
    sort_column = _SORT_COLUMNS[sort]
    statement = (
        select(
            CatalogueEntry.business_key,
            CatalogueEntry.preferred_term,
            CatalogueEntry.status,
            CatalogueEntry.specimen_unconstrained,
            CatalogueEntry.updated_at,
            CatalogueEntry.row_version,
            sort_column.label("sort_value"),
        )
        .where(CatalogueEntry.status.in_(MAINTENANCE_STATUSES))
        .where(*filter_predicates(filters))
        .order_by(sort_column.asc(), CatalogueEntry.business_key.asc())
        # One more row than asked for: its existence decides `next_cursor`,
        # matching every other keyset page in this codebase.
        .limit(limit + 1)
    )
    if after_sort_value is not None and after_key is not None:
        statement = statement.where(
            or_(
                sort_column > after_sort_value,
                and_(sort_column == after_sort_value, CatalogueEntry.business_key > after_key),
            )
        )
    return statement


def list_entries_any_status(
    session: Session,
    *,
    sort: SortName = "business_key",
    limit: int,
    after: str | None = None,
    filters: Sequence[FilterSelection] = (),
) -> ListingPage:
    """One keyset page of entries of *any* status, ordered by `sort` then
    `business_key` (issue #287) - the maintenance counterpart to
    `queries.list_entries`, whose own docstring's paging and cursor
    reasoning applies identically here (same "one extra row" trick, same
    filter composition). The differences are the status scope
    (`MAINTENANCE_STATUSES` rather than `queries.PUBLIC_STATUSES`) and the
    sortable, composite-keyset cursor - see the module docstring.

    Raises `MalformedListingCursorError` for an `after` value this module
    did not produce, and its `ListingCursorMismatchError` subclass for one
    produced for a different `sort` or filter set.
    """
    after_sort_value: Any = None
    after_key: str | None = None
    if after is not None:
        after_sort_value, after_key = _parse_cursor(after, sort=sort, filters=filters)

    statement = build_listing_statement(
        sort=sort,
        filters=filters,
        after_sort_value=after_sort_value,
        after_key=after_key,
        limit=limit,
    )
    rows = session.execute(statement).all()

    listing_rows = tuple(
        ListingRow(
            business_key=row.business_key,
            preferred_term=row.preferred_term,
            status=row.status,
            specimen_unconstrained=row.specimen_unconstrained,
            updated_at=row.updated_at,
            row_version=row.row_version,
        )
        for row in rows
    )
    if len(listing_rows) > limit:
        page = listing_rows[:limit]
        boundary = rows[limit - 1]
        cursor = _format_cursor(
            boundary.sort_value, boundary.business_key, sort=sort, filters=filters
        )
        return ListingPage(rows=page, next_cursor=cursor)
    return ListingPage(rows=listing_rows, next_cursor=None)
