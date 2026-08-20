# ADR-0022: Designation storage - the three preferred-term strings, and term hygiene

**Status:** Accepted
**Date:** 2026-08-20

## Context

Issue #47 adds designation storage (FR-04: synonyms as rows, never a delimited string;
FR-85/FR-24: computed, never-stored preferred-term length) on top of `catalogue_entry`
(#46). PRD §6.2 already put `preferred_term` on `catalogue_entry`; PRD §6.3 separately
describes a `Designation` table whose `use` enum includes `'preferred'`. Taken together
these read as if the catalogue's preferred term could live in either place, and a design
that let it live in both would reintroduce exactly the defect class this platform exists
to eliminate: two copies of one string that can silently disagree.

There is also a second, easy-to-miss ambiguity. The catalogue publishes more than one
preferred-term-shaped string:

- The **RCPA/catalogue preferred term** - the term an administrator maintains, and one
  that exists *before* any SNOMED CT code is bound to the entry.
- The **SNOMED CT-AU preferred term** and **Fully Specified Name** - both served by the
  terminology server once a code binding exists (#48), stored exactly as served (FR-82),
  never editable by this platform.

A design that didn't separate these explicitly would eventually end up copying a served
label into an editable table "for convenience" - which breaks FR-82's as-served
guarantee the moment anyone edits it.

Separately, FR-63 requires "normalisation on ingestion and prohibition at entry" for
invisible-character defects (PRD Appendix A.1); #47 needed a decision on how strict that
prohibition is for a term typed directly into the platform, as opposed to a cell
ingested from the legacy spreadsheet.

## Decision

### Three strings, three homes, one editable

| String | Home | Editable? |
|---|---|---|
| RCPA/catalogue preferred term | `catalogue_entry.preferred_term` (issue #46) | Yes |
| SNOMED CT-AU preferred term | `code_binding.au_preferred_term` (issue #48) | No - as served (FR-82) |
| SNOMED CT Fully Specified Name | `code_binding.fsn` (issue #48) | No - as served (FR-82) |

`catalogue_entry.preferred_term` stays the single, authoritative home for the
catalogue's en-AU preferred term. `designation` holds only catalogue-authored synonyms
and non-en-AU preferred-term variants; it never mirrors a SNOMED CT-served label.

A database `CHECK` constraint enforces this rather than leaving it to application
discipline: `ck_designation_no_en_au_preferred` forbids
`use = 'preferred' AND language = 'en-AU'` on `designation`. A non-en-AU
catalogue-authored preferred variant (e.g. `use='preferred', language='mi-NZ'`) is still
permitted - the constraint is about where the *catalogue's own* en-AU term lives, not
about forbidding preferred designations generally.

`backend/tests/test_catalogue_designations.py` asserts the boundary directly:
`designation` has no `au_preferred_term`/`fsn` column, and
`nptc.catalogue.designations` has no dependency on the (not-yet-built) code-binding
module - so a future change that starts copying a served label into a designation row
fails a test rather than passing review unnoticed.

**A direct consequence: FR-85's `Length` must be computed against
`catalogue_entry.preferred_term`, not any `designation` row.** PRD §6.5 is explicit that
`Length` is "the character count of the RCPA preferred term" - and since that string
never lives on `designation` (the row above), the computed-length property has to sit
on `CatalogueEntry`, not `Designation`, or it silently computes the wrong entity's
length. `CatalogueEntry.length` (`nptc.catalogue.term_hygiene.preferred_term_length`
applied to `preferred_term`) is the field FR-85 actually publishes;
`Designation.length` applies the same computation to a designation's own `term` for
the same reason, but is a distinct, non-authoritative figure.

### Rejected: mirror the preferred term into both tables

Insert a `use='preferred', language='en-AU'` designation row alongside
`catalogue_entry.preferred_term`, kept in sync by a single service-layer write path.
Matches PRD §6.3 literally and would let search/collision logic (#49) treat every
preferred term uniformly as a designation row.

Rejected because it creates two copies of the same string with a synchronisation
obligation between them - precisely the defect class (a preferred term that can drift
from what it is supposed to describe) FR-85's own rationale calls out for `Length`, now
recreated for the term itself. A sync bug here is a data-integrity defect with no
constraint to catch it: nothing stops the two copies from disagreeing after a partial
write, a bug in the sync path, or a future direct-SQL fix that only touches one side.

### Rejected: drop `catalogue_entry.preferred_term`, designation is the only home

Remove the column #46 already shipped, treat every designation table as authoritative
including the en-AU preferred row.

Rejected because it contradicts PRD §6.2's core-column list, loses the `NOT NULL`
guarantee that every entry has a preferred term (a `designation` row is optional - an
entry can exist before it has any), unwinds shipped #46 work (grants, audit policy,
`EntryChanges`, `ConflictReport`), and leaves an unmapped entry (the common case before a
code binding is created) with no preferred term at all.

### Term hygiene: clean the normalisable, reject the ambiguous

`nptc.catalogue.term_hygiene.clean_term` (called from both `CatalogueEntry`'s and
`Designation`'s own `@validates("preferred_term"/"term")` hooks - a single function
applied to both fields, since FR-85's published length depends on the same cleaning
having already happened to `CatalogueEntry.preferred_term`) collapses every
normalisable space - a non-breaking space, a narrow no-break space (PRD Appendix A.1) -
to an ordinary space and strips the edges, via `nptc_shared.text.
normalise_for_comparison` (the same function the P0 transform and FR-05 collision
detection already share, ADR-0001). This mirrors FR-71's own doctrine: a normalisable
space has exactly one deterministic repair, so correcting it silently is correct, not a
defect being hidden.

Anything that survives that pass - a zero-width space, a bidi override, a genuine
control character - has no single correct repair, so it is rejected
(`TermCleaningError`) rather than silently dropped or silently stored. This is FR-63's
"prohibition at entry" half: the platform does not merely clean up defects on ingestion
from the legacy spreadsheet, it refuses to accept new ones typed directly into the
system, on either field. The error message quotes the offending character escaped
(`nptc_shared.text.escape_invisible`), never raw, per NFR-38 test 2.

### Rejected: reject every invisible character outright

Refuse any term containing a non-breaking space rather than cleaning it, on the theory
that "prohibition at entry" should be absolute.

Rejected because it makes the ADR-0010 seeded-import path need its own separate cleaning
step ahead of storage (since seeded terms come from the legacy spreadsheet's own
Appendix A.1 defects), and forces every test and caller to pre-clean a term before it can
ever be accepted - work FR-71 already establishes has exactly one correct, mechanical
answer.

### Rejected: clean everything silently, including zero-width/bidi characters

Strip or collapse every invisible character, with no rejection path at all.

Rejected because a zero-width space, bidi override, or control character has no single
correct repair - silently deciding one on the caller's behalf risks changing what the
term actually says, contradicting `nptc_shared.text`'s own established doctrine for
`is_normalisable_space`.

## Consequences

- Search, export, and #49's collision detection can treat `catalogue_entry.preferred_term`
  as the one place to look for the catalogue's own en-AU preferred term, with no need to
  reconcile it against a `designation` row.
- Code bindings (#48) remain the only path a SNOMED CT-served label reaches this
  platform through; `designation` and `code_binding` stay structurally unable to
  disagree about which one owns which string.
- A term typed directly into the platform is held to the same normalisation contract as
  one ingested from the spreadsheet (FR-63), with no second, looser path for
  operator-entered data.
