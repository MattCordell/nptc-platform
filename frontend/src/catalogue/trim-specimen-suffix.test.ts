import { describe, expect, it } from "vitest";

import { trimSpecimenSuffix } from "./trim-specimen-suffix.ts";

describe("trimSpecimenSuffix (FR-04)", () => {
  it.each([
    ["Serum specimen", "Serum"],
    ["Serum SPECIMEN", "Serum"],
    ["Urine  specimen", "Urine"],
    ["Whole blood specimen", "Whole blood"],
  ])("removes a trailing specimen word from %s", (term, expected) => {
    expect(trimSpecimenSuffix(term)).toBe(expected);
  });

  it.each([["Specimen"], ["specimen"]])(
    "keeps a bare %s rather than returning an empty string",
    (term) => {
      expect(trimSpecimenSuffix(term)).toBe(term);
    },
  );

  it.each([
    ["Specimen collection kit"],
    ["Serum"],
    ["Biospecimen"],
    ["Specimen container specimens"],
  ])("leaves %s alone, because specimen is not its last word", (term) => {
    expect(trimSpecimenSuffix(term)).toBe(term);
  });
});
