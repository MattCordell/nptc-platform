# Bulk reclassify

**Bulk reclassify is not available on the screen.** The selection checkboxes, the
**Reclassify selected** button and the results panel were removed when
**Administration → Catalogue** was rebuilt as a search that opens one edit form per entry.

To change a property on several entries today, open each entry from the
[catalogue list](finding-entries.md) and use its [editing screen](editing-an-entry.md).

The server still accepts a bulk write, `POST /catalogue/entries/bulk/properties/{key}`, for
callers that use the API directly. It behaves as it always has: it replaces the property's
whole value set on every entry you name, and it reports each entry as applied, unchanged,
conflicted or not found.

A replacement screen is deferred. It would need its own design, so it is not part of the
current catalogue maintenance screens.
