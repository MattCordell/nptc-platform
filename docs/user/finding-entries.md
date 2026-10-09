# Finding and filtering entries for editing

**Administration → Catalogue** lists every catalogue entry — draft, active, deprecated and
withdrawn — and is where you find the one you want to edit. You need the Administrator
role to open it, the same as the [editing screen](editing-an-entry.md) it leads to.

## Searching and browsing

Leave the search box empty to browse the whole catalogue in code order. Type a term or a
SNOMED CT code and press **Search** to rank results by relevance instead — the identical
matching the public catalogue search uses, just over every status rather than published
entries alone.

The list has no "page 3" to jump to. **Next page** moves forward from where you are, and
**Previous page** goes back through the pages you have just viewed. On the last page the
list says "No more results" and **Next page** is unavailable. If you open the list from a
link, reload it part-way through, or use your browser's Back or Forward button,
**Previous page** is unavailable until you move forward again; use your browser's Back
button to return from there.

## Filtering

**Administration → Catalogue** has the same search box and filters as the public
[catalogue search](searching-the-catalogue.md). Three filters sit under the search box:
**Status**, **Discipline** and **Specimen**. Open one, type to narrow its list, and pick
as many values as you want. Picking a second value within one filter broadens that
filter, while picking a value in a different filter narrows the result further.

Each filter lists the values the property registry offers, with no counts. The
administration list does not count entries per value, because counting the whole
catalogue on every change is not something a plain list needs to pay for. The Discipline
and Specimen filters appear only while the property registry holds that property as
active, filterable and coded, so a deprecated property loses its filter.

Every active filter also shows as a chip under the filters. Choose a chip to remove its
filter, or **Clear all filters** to remove them all. A filter in a link that has no control
here, such as a property an administrator has since removed, still applies and still shows
as a chip, so you can always clear it.

**Sort by** orders a browsed list by identifier, requesting term, last change or status.
While you search it shows **Relevance** and is unavailable, because a search ranks its own
results.

**Your filters, search term, sort and page position are all part of the page's link** — copy
it from your browser's address bar and it takes you (or anyone else with access) straight
back to the same view, on reload or on a different day.

## Finding an entry to edit

Each row's requesting term is a link straight to that entry's
[editing screen](editing-an-entry.md) — there is no need to know or type its URL. The row
also shows the entry's identifier, its status and its disciplines, so you can tell a draft
from a published entry before you open it. A row marked **Open finding** has a validation
finding that still needs attention.

The list has no checkboxes. Bulk reclassify is not available on this screen; see
[Bulk reclassify](bulk-reclassify.md).

## If something goes wrong

**"Catalogue entries could not be loaded."** The list itself failed to load — try again, or
contact an administrator if it persists.

**"... could not be refreshed just now, so what follows may be out of date."** The list
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. What you see may no longer be current. Sign in again before relying on it.

**"You cannot view this screen with your current sign-in."** See the note on multi-factor
authentication in [Editing an entry](editing-an-entry.md).
