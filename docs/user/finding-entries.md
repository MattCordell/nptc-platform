# Finding and filtering entries for editing

**Administration → Catalogue** lists every catalogue entry — draft, active, deprecated and
withdrawn — and is where you find the one you want to edit. You need the Administrator
role to open it, the same as the [editing screen](editing-an-entry.md) it leads to.

## Searching and browsing

Leave the search box empty to browse the whole catalogue in code order. Type a term or a
SNOMED CT code and press **Search** to rank results by relevance instead — the identical
matching the public catalogue search uses, just over every status rather than published
entries alone.

The list has no "page 3" to jump to. **Next page** moves forward from where you are; there
is no equivalent button to go back — use your browser's Back button, which returns you to
the page you were on.

## Filtering

The filter panel narrows the list to entries matching everything you tick — ticking a
second value within one filter (for example, two disciplines) broadens that filter, while
ticking a value in a different filter narrows the result further. **Status** is always
available; which other properties appear depends on what an administrator has marked
filterable in the property registry, so the panel can change over time with no update to
this page.

Filtering while searching also gives you result counts per value, showing how many entries
each option would leave if you selected it — browsing does not, since counting the whole
catalogue on every keystroke is not something a plain list needs to pay for.

**Your filters, search term and page position are all part of the page's link** — copy it
from your browser's address bar and it takes you (or anyone else with access) straight back
to the same filtered view, on reload or on a different day.

## Finding an entry to edit

Each row's code is a link straight to that entry's [editing screen](editing-an-entry.md) —
there is no need to know or type its URL. The row also shows its status, so you can tell a
draft from a published entry before you open it.

## Selecting rows

Tick a row's checkbox to select it, or the checkbox in the column header to select every
row currently on screen. The number of rows selected is announced as you change it, for
anyone using a screen reader to follow along.

Once at least one row is selected, a **Reclassify selected** button appears above the
list — see [Bulk reclassify](bulk-reclassify.md) for setting one property to one value
across everything you have selected in a single step.

Your selection carries across pages as you page forward, but starting a new search or
changing a filter clears it: at that point you are choosing from a different set of
entries, and keeping the old selection would risk acting on rows you never actually looked
at.

## If something goes wrong

**"Catalogue entries could not be loaded."** The list itself failed to load — try again, or
contact an administrator if it persists.

**"... could not be refreshed just now, so what follows may be out of date."** The list
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. What you see may no longer be current. Sign in again before relying on it.

**"You cannot view this screen with your current sign-in."** See the note on multi-factor
authentication in [Editing an entry](editing-an-entry.md).
