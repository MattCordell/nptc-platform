"""The all-status catalogue listing for the maintenance surface (issue #266,
FR-14, FR-15, FR-16, FR-36, FR-44).

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

**The substrate was already built for this.** Both `catalogue_entry`
trigram/full-text indexes (`nptc.db.models.catalogue_entry`) are
deliberately non-partial on `status`, and `nptc.catalogue.search`'s entry
status filter is bound as `:statuses` rather than written as the literal
`'active'` the way the `designation`/`code_binding` branches are - both
recorded in their own modules as being for this issue's benefit. This
module is the query surface that finally uses them; `nptc.catalogue.search`
threads the same `statuses` argument through for the search half.

The listing query itself is `queries.list_entries` with one line changed
(the status tuple) - duplicated rather than parameterising `list_entries`,
because `queries.py`'s rule one is that `PUBLIC_STATUSES` is not merely the
*default* status filter, it is the *only* one that module ever applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.catalogue.facets import FilterSelection, filter_predicates
from nptc.catalogue.queries import EntryPage
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus

__all__ = ["MAINTENANCE_STATUSES", "list_entries_any_status"]

#: Every status a `catalogue_entry` row may hold, derived from the enum
#: rather than hand-listed - see the module docstring.
MAINTENANCE_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in CatalogueEntryStatus
)


def list_entries_any_status(
    session: Session,
    *,
    limit: int,
    after: str | None = None,
    filters: Sequence[FilterSelection] = (),
) -> EntryPage:
    """One keyset page of entries of *any* status, ordered by `business_key`
    - the maintenance counterpart to `queries.list_entries`, whose own
    docstring's paging and cursor reasoning applies identically here (same
    ordering column, same "one extra row" trick, same filter composition).
    The only difference is the status scope: `MAINTENANCE_STATUSES` rather
    than `queries.PUBLIC_STATUSES`.
    """
    statement = (
        select(CatalogueEntry)
        .where(CatalogueEntry.status.in_(MAINTENANCE_STATUSES))
        .where(*filter_predicates(filters))
        .order_by(CatalogueEntry.business_key)
        .limit(limit + 1)
    )
    if after is not None:
        statement = statement.where(CatalogueEntry.business_key > after)

    rows = tuple(session.execute(statement).scalars().all())
    if len(rows) > limit:
        page = rows[:limit]
        return EntryPage(entries=page, next_cursor=page[-1].business_key)
    return EntryPage(entries=rows, next_cursor=None)
