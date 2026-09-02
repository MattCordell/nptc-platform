import { describe, expect, it } from "vitest";

import { splitSynonyms } from "./split-synonyms.ts";

/**
 * The first three cases are PRD Appendix A's own defect strings, quoted
 * verbatim from `backend/tests/test_catalogue_designations.py`'s
 * `test_sample_defect_strings_become_individual_rows_with_no_empty_row`.
 * That is the point: this module is a mirror of
 * `nptc_transform.cell_defects.split_synonyms` (ADR-0029), and sharing the
 * fixtures is what makes a divergence show up here as a failure on a
 * recognisable string rather than as silent drift.
 */
describe("splitSynonyms", () => {
  it.each([
    ["ADA RBC, ADA red cells", ["ADA RBC", "ADA red cells"]],
    ["Zovirax;;Cyclir", ["Zovirax", "Cyclir"]],
    ["Aciclovir ; Acyclovir ;  ", ["Aciclovir", "Acyclovir"]],
  ])(
    "splits the sample cell %j into individual terms with no empty part",
    (cell, expected) => {
      expect(splitSynonyms(cell)).toEqual(expected);
    },
  );

  it("prefers the semicolon when a cell contains both delimiters", () => {
    // The Python checks for ";" first for the same reason: a term is allowed
    // to contain a comma, so once a semicolon is present it is the delimiter
    // the author meant and the commas are part of the terms.
    expect(splitSynonyms("Smith, factor V; Leiden")).toEqual([
      "Smith, factor V",
      "Leiden",
    ]);
  });

  it("keeps a bare comma inside a term, splitting only on comma-space", () => {
    // `_SYNONYM_FALLBACK_DELIMITER` is ", " and not ",", so this is one term.
    expect(splitSynonyms("1,25-dihydroxyvitamin D")).toEqual(["1,25-dihydroxyvitamin D"]);
  });

  it("returns no terms for a cell that is empty or only delimiters", () => {
    // The principal failure mode FR-04 names: a cell that would otherwise
    // produce an empty designation row. It must produce none at all, so the
    // caller has nothing to submit rather than something blank to submit.
    expect(splitSynonyms("")).toEqual([]);
    expect(splitSynonyms("   ")).toEqual([]);
    expect(splitSynonyms(";;;")).toEqual([]);
  });

  it("drops a term that is only a non-breaking space", () => {
    // PRD Appendix A.1's invisible-character defect class arriving by paste.
    // The backend's `clean_term` would refuse it as an empty term (a 422);
    // dropping it here means the editor never submits one to be refused.
    expect(splitSynonyms("Ferritin; ;Serum ferritin")).toEqual([
      "Ferritin",
      "Serum ferritin",
    ]);
  });

  it("preserves the order the cell gave, and does not deduplicate", () => {
    // Deduplication is the server's, on the comparison key `collision_key`
    // folds - `add_synonyms` dedupes and re-orders by that key. Doing either
    // here would be a second, weaker implementation of a rule that already
    // has an authoritative one.
    expect(splitSynonyms("Beta;Alpha;beta")).toEqual(["Beta", "Alpha", "beta"]);
  });
});
