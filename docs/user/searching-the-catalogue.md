# Searching the catalogue

The catalogue page at `/catalogue` lists every published test in the National Pathology
Test Catalogue. You can browse it, search it and filter the results. You do not need an
account.

## Browsing

Open the page with the search box empty to browse. The list shows published tests in
catalogue order, 50 to a page.

## Searching

Type a test name or a SNOMED CT code in **Search term or SNOMED CT code**, then press
Enter or choose **Search**. The best matches come first. Spaces at the start and end of
what you type are ignored.

To go back to browsing, clear the box and search again.

## What each row shows

| Column | What it shows |
|---|---|
| **Requesting term** | The test's name. Choose it to open the test's own page. **Open finding** appears beside the name when an automated check has flagged the test. See [What the finding indicator means](viewing-the-finding-indicator.md). |
| **Discipline** | Each discipline recorded for the test. **None recorded** means none is. |
| **Specimen** | Each specimen recorded for the test, by its SNOMED CT-AU preferred term, without a closing "specimen" (so "Serum specimen" shows as "Serum"). **None recorded** means none is. |
| **SNOMED CT FSN** | The fully specified name of the code the test is bound to now, without its closing tag in brackets (so "Microscopy (procedure)" shows as "Microscopy"). **No code** means the test has none. |

The list does not show the code itself. Open the test's own page to see its code, the full
name with its tag, and its code history. A code that has been retired never appears in
this list.

A specimen added by hand shows the wording that was entered. Specimens loaded from the
workbook show the SNOMED CT-AU preferred term.

## Filtering

Filters appear while you search. Each filter shows its values with a count of how many
results each value gives you.

- Choose a value to apply it. Choose it again to remove it.
- Choosing two values in one filter, such as two disciplines, gives you tests with either.
- Choosing values in two different filters gives you only tests that match both.
- A filter with many values is a drop-down list. Pick a value from it to apply it.

Each filter you apply appears below the filters as a button with a cross. Choose it to
remove that filter, or choose **Clear all filters** to remove them all.

Some filters have too many values to list. The page then says the filter shows only its
most common values. Narrow your search to see the others.

While you browse, the page offers no filters. A filter already in the page's link still
applies, and appears as a removable button showing its raw name and value.

## Moving between pages

The list has no page numbers and no total. **Next page** moves forward, and
**Previous page** goes back through the pages you have viewed. On the last page the list
says "No more results".

**Previous page** is unavailable after you reload the page or open it from a link. Use your
browser's Back button instead.

## Sharing a search

Your search, filters and page position are all in the page's link. Copy it from your
browser's address bar to return to the same results later, or to send them to someone
else.

## When nothing matches

The list says no entries match. If filters are applied, choose **Clear all filters** to
remove them and widen the results.

## If something goes wrong

If the catalogue cannot be loaded, the page says so. Try again in a moment, or change your
search. If a filter in a link is no longer offered, the page shows why. Remove that filter
to continue.

If the page has results but cannot refresh them, it keeps showing them and warns you they
may be out of date.

## Using it with a keyboard or screen reader

Press Tab to move through the page: the search box, the filters, the results and the page
controls. A visible ring shows where you are. Press Enter or Space to choose a filter value
or a page control.

Screen readers announce how many results are on the page, and whether more follow. They
also announce when nothing matches and when something goes wrong.
