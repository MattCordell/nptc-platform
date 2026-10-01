"""The all-status catalogue listing for the maintenance surface (FR-14,
FR-15, FR-16, FR-36, FR-44), with its server-side sort (FR-16's second
acceptance criterion).

`nptc.catalogue.queries` applies `PUBLIC_STATUSES` as "the only status
filter", so `queries.list_entries` takes no `statuses=` parameter. This
module holds the one query that needs a different scope.

**Every status.** `MAINTENANCE_STATUSES` is every `CatalogueEntryStatus`, for
an administrator holding `Permission.CATALOGUE_EDIT_PUBLISHED`: `draft`,
`deprecated` and `withdrawn` entries must be reachable to be worked on. It is
derived from the enum, so a new status is covered on the day it is added. The
`nptc.catalogue.length_report` module docstring gives the reasoning for
counting every status in a report.

**Sort.** `GET /catalogue/admin/entries` offers four columns (`SortName`).
Only `business_key` is unique, so paging by any other column needs a
composite keyset over `(sort_value, business_key)`, with `business_key` as
the tie-break. ADR-0024 (amendment of 2026-09-10) records the cursor design
and why no index accompanies it. `status` has four distinct values, too few
for the planner to use an index on it in any case.

- `status` orders by lifecycle (`draft`, `active`, `deprecated`,
  `withdrawn`), not alphabetically. `_SORT_COLUMNS["status"]` is a `CASE`
  over `MAINTENANCE_STATUSES`, which follows the enum's declaration order, so
  the cursor's `status` sort value is that integer position, not the string.
- `preferred_term` sorts by `preferred_term_key`, FR-05's fold
  (`nptc_shared.similarity.collision_key`: case-folded, punctuation as a
  token separator). `17-OHP` and `17 OHP` therefore sort adjacently, not in
  byte-exact alphabetical order. This is deliberate: it is the fold collision
  detection already applies. A microcopy note on the sort label is the
  considered remedy for a reader who expects literal order.

**Cursor.** `"<sort value>:<digest>:<business key>"` for every sort,
including `business_key`, where the sort value and the key are the same
string. `_parse_cursor` mirrors `nptc.catalogue.search`, and splits with
`rpartition` from the right, twice: the digest (hex) and the business key
(`BUSINESS_KEY_PATTERN`) never contain a colon, but an ISO-8601 `updated_at`
sort value does.

The digest binds the sort column, because `sort_value > :after` means
something different under another ordering. It also binds the filter set, so
that "what does this cursor refuse" has one answer for both routes; keyset
correctness does not need it, since a sort value is intrinsic to the row. It
does not bind the status scope, which is a single constant on this route.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final, Literal
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Select, and_, case, or_, select
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

#: Every status a `catalogue_entry` row may hold, derived from the enum.
MAINTENANCE_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in CatalogueEntryStatus
)

#: The orderings `GET /catalogue/admin/entries` accepts. A `Literal`, so
#: FastAPI publishes the values in `docs/api/openapi.json` and 422s any other
#: before handler code runs.
SortName = Literal["business_key", "preferred_term", "updated_at", "status"]

#: Each `SortName`'s comparison column. `preferred_term` sorts by
#: `preferred_term_key`, the indexed column `nptc.catalogue.collisions`
#: compares on. `status` is a `CASE` giving lifecycle order.
_SORT_COLUMNS: Final[dict[SortName, ColumnElement[Any]]] = {
    "business_key": type_cast("ColumnElement[Any]", CatalogueEntry.business_key),
    "preferred_term": type_cast("ColumnElement[Any]", CatalogueEntry.preferred_term_key),
    "updated_at": type_cast("ColumnElement[Any]", CatalogueEntry.updated_at),
    "status": case(
        *(
            (CatalogueEntry.status == status, index)
            for index, status in enumerate(MAINTENANCE_STATUSES)
        ),
        # The `CHECK` constraint (`_STATUS_CHECK_SQL`) is a literal held
        # separately from the enum, so the two can drift. Without `else_`, an
        # unrecognised status sorts as `NULL`, and `_format_sort_value` raises
        # `TypeError` if that row becomes a page boundary: a 500. This puts
        # such rows after every recognised status.
        else_=len(MAINTENANCE_STATUSES),
    ),
}

#: A digest and a `business_key` never contain `:`, so the split is
#: unambiguous (see `nptc.catalogue.search._CURSOR_SEPARATOR`); parsing is
#: from the right because an ISO-8601 sort value does.
_CURSOR_SEPARATOR: Final[str] = ":"

#: 8 bytes of BLAKE2s, unkeyed as in `nptc.catalogue.search`: a listing cursor
#: is not a capability, so there is only a client-bug replay to detect.
_CURSOR_DIGEST_BYTES: Final[int] = 8


class MalformedListingCursorError(ValueError):
    """Raised for an `after` cursor this module did not mint - the counterpart
    of `nptc.catalogue.search.MalformedSearchCursorError`. Falling back to
    page one would make a client's paging loop restart forever."""

    http_status: ClassVar[int] = 422


class ListingCursorMismatchError(MalformedListingCursorError):
    """Raised for a well-formed cursor minted for a *different* sort or
    filter set - the counterpart of
    `nptc.catalogue.search.SearchCursorQueryMismatchError`. A subclass, so
    `nptc.api.errors` maps it to the same 422. Replaying a keyset predicate
    over another ordering would select a window that is nobody's next page."""


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
    """The cursor's fingerprint of the sort column and the filter set."""
    material = f"{len(sort.encode())}:{sort}{filter_digest_material(filters)}"
    return hashlib.blake2s(material.encode("utf-8"), digest_size=_CURSOR_DIGEST_BYTES).hexdigest()


def _format_sort_value(sort: SortName, sort_value: Any) -> str:
    """The cursor's text form of one row's sort value, matched by
    `_parse_sort_value`: `isoformat()` for `updated_at`, the lifecycle-order
    integer for `status`, the raw string for the other two.

    Raises `TypeError` rather than asserting, so the check survives
    `python -O`: this runs on a row this module's own statement returned, so
    a mismatch is a bug here, not caller input."""
    if sort == "updated_at":
        if not isinstance(sort_value, datetime):
            raise TypeError(f"expected a datetime sort_value for sort={sort!r}, got {sort_value!r}")
        return sort_value.isoformat()
    if sort == "status":
        if not isinstance(sort_value, int):
            raise TypeError(f"expected an int sort_value for sort={sort!r}, got {sort_value!r}")
        return str(sort_value)
    return str(sort_value)


def _parse_sort_value(sort: SortName, raw: str, *, cursor: str) -> Any:
    if sort == "updated_at":
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise MalformedListingCursorError(
                f"listing cursor {cursor!r} does not begin with a well-formed timestamp"
            ) from None
        # `updated_at` is `DateTime(timezone=True)` and every minted cursor
        # carries an offset. A naive value would be read in the session's own
        # timezone and silently compare against a shifted window.
        if parsed.tzinfo is None:
            raise MalformedListingCursorError(
                f"listing cursor {cursor!r} does not begin with a timezone-aware timestamp"
            )
        return parsed
    if sort == "status":
        try:
            value = int(raw)
        except ValueError:
            raise MalformedListingCursorError(
                f"listing cursor {cursor!r} does not begin with a well-formed status order value"
            ) from None
        # `int()` alone accepts `"+1"`, `" 1 "` and any magnitude, none of which
        # this module mints. The upper bound is the `CASE` expression's
        # `else_` value in `_SORT_COLUMNS`.
        if not 0 <= value <= len(MAINTENANCE_STATUSES):
            raise MalformedListingCursorError(
                f"listing cursor {cursor!r} does not begin with a status order value "
                "this module mints"
            )
        return value
    return raw


def _format_cursor(
    sort_value: Any, business_key: str, *, sort: SortName, filters: Sequence[FilterSelection]
) -> str:
    return _CURSOR_SEPARATOR.join(
        (_format_sort_value(sort, sort_value), _digest(sort, filters), business_key)
    )


def _parse_cursor(
    cursor: str, *, sort: SortName, filters: Sequence[FilterSelection]
) -> tuple[Any, str]:
    """`"<sort value>:<digest>:<business key>"`, parsed from the right: an
    ISO-8601 `updated_at` sort value contains `:` itself."""
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
    # `compare_digest`, not `==`, as in `search._parse_cursor`.
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

    Public so `backend/tests/test_db_search_index.py` can `EXPLAIN` the
    statement this function builds, not a copy of it.

    `business_key` is always the tie-break, including when `sort` is itself
    `"business_key"`. The keyset predicate is the composite
    `(sort_value, business_key) > (after_sort_value, after_key)`; for
    `sort="business_key"` the two values are equal, so it reduces to
    `queries.list_entries`'s `business_key > after`.
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
        # One extra row decides `next_cursor`.
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
    `business_key` - the maintenance counterpart to `queries.list_entries`,
    whose paging and filter reasoning applies identically here. The
    differences are the status scope (`MAINTENANCE_STATUSES`, not
    `queries.PUBLIC_STATUSES`) and the composite-keyset cursor.

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
