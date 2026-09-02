# ADR-0030: Domain logic at the browser boundary - mirror the function, share the fixtures

**Status:** Accepted
**Date:** 2026-09-02

## Context

ADR-0001 and FR-74 settle how this repository avoids a second, divergent implementation
of a domain rule: `shared/` (`nptc_shared`) is imported by *both* `backend/` and
`transform/`, so SCTID validation, Verhoeff, text normalisation, similarity and BCP-47
well-formedness each exist exactly once. `nptc_shared.similarity`'s own module docstring,
`shared/src/nptc_shared/language.py`'s, and ADR-0022's term-hygiene section all restate
the same doctrine.

Issue #149 is the first change that needs one of those rules to run in a browser, and
`shared/` has no seam that reaches there. The specific rule is FR-04's synonym split.
`POST /catalogue/entries/{business_key}/designations` takes `terms` **already split**, and
`nptc.catalogue.designations.add_synonyms` deliberately does no splitting of its own -
`backend/tests/test_catalogue_designations.py`'s FR-04 test calls
`nptc_transform.cell_defects.split_synonyms` itself and passes the result in, treating the
split as the caller's job. On the seeding path that caller is the P0 transform. On the
editing path it is the edit screen, in the browser, and the pasted-cell case is an explicit
acceptance criterion of #149 (`Zovirax;;Cyclir` must become two rows and no empty row).

This is not a one-off. Issue #150 needs `nptc_shared.sctid`'s format check and Verhoeff
check digit in the form, before a request is sent - its own acceptance criteria say so.
Whatever is decided here is the precedent for that, so it is worth deciding once rather
than twice.

## Decision

**Mirror the function in TypeScript, and share the fixtures rather than the code.**

Three conditions, all of which the FR-04 split meets and which a future candidate must
also meet:

1. **The mirror is small enough to read against the original in one sitting.**
   `frontend/src/catalogue/split-synonyms.ts` is the same four operations as
   `cell_defects.split_synonyms`: choose the delimiter, split, trim, drop empties. A rule
   that cannot be restated that compactly does not get mirrored - it stays server-side and
   the client waits for the response.
2. **The mirror's test uses the *same fixtures*, quoted verbatim, as the Python test of
   the original.** `split-synonyms.test.ts` opens with the three PRD Appendix A strings
   from `test_sample_defect_strings_become_individual_rows_with_no_empty_row`. This is what
   the ADR is actually buying: a divergence surfaces as a failing assertion on a
   recognisable string, in a file whose comment names its counterpart, rather than as
   behaviour that quietly drifts apart over a year.
3. **The server stays the authority.** The mirror exists to make the interaction correct
   and immediate, never to make a decision the server then trusts. The backend re-cleans,
   re-validates and re-checks every term it is sent (`clean_term`, the two partial unique
   indexes, `assert_no_error_collisions`); a browser that skipped the split entirely would
   produce a worse experience, not an unsafe one.

Where the two languages genuinely differ, the mirror closes the gap explicitly rather
than inheriting a platform default. `String.prototype.trim()` is not `str.strip()`: Python
strips `U+0085` and `U+001C`-`U+001F`, which JavaScript leaves; JavaScript trims `U+FEFF`,
which Python's `str.isspace()` excludes. Every one of those is a PRD Appendix A.1
character, so the platform default would have diverged in exactly the input class the
mirror exists to handle - the one place the drift would have been invisible.
`split-synonyms.ts` therefore spells the Python whitespace set out as its own character
class, and the fixture naming those codepoints is asserted on both sides of the wire.

Two general points fall out of that, for the mirrors still to come. A shared fixture set
has to include the *boundary* cases and not only the representative ones, because the
representative cases are precisely the ones two implementations agree on by accident. And
"the same standard-library function exists in both languages" is a claim to check rather
than assume - `trim`/`strip`, `toLowerCase`/`casefold` and `normalize`/`unicodedata.
normalize` all differ in ways that matter to terminology work.

## Rejected alternatives

**Split server-side: accept a raw pasted cell on the add route.** Genuinely the "one
implementation" answer, and the first thing to consider. Rejected on two counts. It would
make `backend/` import `transform/`, which ADR-0001's layering does not have - the
dependency runs backend to shared and transform to shared, never backend to transform - and
the alternative, lifting `split_synonyms` into `nptc_shared`, moves a spreadsheet-cell
repair into the module meant to hold rules the *catalogue* has, not rules the *legacy
workbook* has. It would also change the shape of a route shipped in #224 for every existing
client, to serve one screen. And it does not remove the browser-side need in any case: the
editor has to be shown what will be created before submitting, so the split runs in the
browser regardless of who runs it again afterwards.

**Drop the paste affordance: one term per row, typed.** Removes the duplication entirely
and is honest about where the logic lives. Rejected because #149's acceptance criteria name
the paste case specifically, and because it is the wrong way round: the pasted cell is the
*actual* migration path off the spreadsheet this platform replaces. Making the editor
hand-retype what they can paste is how a decade of `;;` defects gets retyped rather than
repaired.

**Generate the TypeScript from the Python.** A transpiler, or emitting a shared rule table
(delimiters, patterns) from Python into a JSON both sides read. Rejected as more machinery
than the problem carries: the rules in question are a handful of constants and four lines
of logic, and a generator plus its CI staleness gate is a larger, more surprising thing to
maintain than the mirror it protects. `docs/api/openapi.json` to
`frontend/src/api/schema.ts` already establishes that this repository *will* generate
across the boundary where the artefact is big enough to justify it; this is the case where
it is not. Revisit if a third and fourth mirror appear, or if one of them is a table rather
than a function - the specimen table (FR-88) would be a genuine candidate.

**Do it, and record the reasoning as a PR review comment.** What was considered first.
Rejected because #150 needs the same decision within days and would have no way to find a
comment on this PR's diff - the specific failure mode ADR-0001's own "read one before
relitigating a stack choice" rule exists to prevent.

## Consequences

- A rule mirrored under this ADR has two homes and a standing obligation: changing the
  Python means changing the TypeScript in the same PR. Nothing enforces that mechanically.
  The shared fixtures are the mitigation and they are a partial one - they catch a
  behaviour change on a *covered* input, not the addition of a new delimiter to one side.
  Both files carry a comment naming the other, so a reader editing either is told.
- `frontend/src/catalogue/` is established as where a mirrored domain rule lives - not
  `frontend/src/api/`, which is transport and generated types, and not inside a component.
  #150's SCTID check belongs there, beside this one, rather than in the binding form.
- The three conditions are a real gate, not a formality. "Re-implement the collision key in
  TypeScript so the screen can warn before saving" fails condition 1, and would fail
  condition 3 in spirit: `collision_key` folds tokens against rules that live in
  `nptc_shared.similarity` for a reason, and a browser-side approximation that disagreed
  with the server would be worse than no warning at all.
