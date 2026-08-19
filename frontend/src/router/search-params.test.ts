import { describe, expect, it } from "vitest";

import {
  validateCatalogueSearch,
  validateLookupSearch,
  validateReleaseCompareSearch,
  validateSignInSearch,
} from "./search-params.ts";

describe("validateCatalogueSearch", () => {
  it("defaults every field when absent", () => {
    expect(validateCatalogueSearch({})).toEqual({ q: "", page: 1, sort: "relevance" });
  });

  it("degrades a malformed page and sort to their defaults rather than throwing", () => {
    expect(validateCatalogueSearch({ page: "not-a-number", sort: "nonsense" })).toEqual({
      q: "",
      page: 1,
      sort: "relevance",
    });
  });

  // The router only ever calls this with strings (every search value comes
  // off the URL as a raw string - see router.tsx's custom parseSearch), so
  // that is what these tests supply, not JS numbers.
  it("passes through valid values", () => {
    expect(validateCatalogueSearch({ q: "glucose", page: "3", sort: "code" })).toEqual({
      q: "glucose",
      page: 3,
      sort: "code",
    });
  });

  it("rejects a page below 1", () => {
    expect(validateCatalogueSearch({ page: "0" })).toEqual({
      q: "",
      page: 1,
      sort: "relevance",
    });
  });

  // TanStack Router calls validateSearch more than once per navigation, and
  // a later call receives this function's own previously-validated output
  // (page as a real number), not the raw URL string. Feeding the output
  // straight back in must reproduce it exactly - this is the regression
  // test for the bug where that second call saw `page: 3` (a number),
  // `asString` rejected it for not being a string, and the result silently
  // fell back to page 1.
  it("is idempotent - validating its own output reproduces it", () => {
    const once = validateCatalogueSearch({ q: "glucose", page: "3", sort: "code" });
    const twice = validateCatalogueSearch(once as unknown as Record<string, unknown>);
    expect(twice).toEqual(once);
  });
});

describe("validateLookupSearch", () => {
  it("defaults to empty strings when absent", () => {
    expect(validateLookupSearch({})).toEqual({ system: "", code: "" });
  });

  // FR-06: a code arriving as a number (e.g. if the router's default
  // JSON-coercing parser were ever restored) must still surface as a string,
  // not be silently accepted as a number.
  it("returns a string for a number-shaped code rather than accepting the number", () => {
    expect(validateLookupSearch({ system: "http://snomed.info/sct", code: 123 })).toEqual(
      { system: "http://snomed.info/sct", code: "" },
    );
  });

  it("passes through a valid system and code unchanged", () => {
    expect(
      validateLookupSearch({ system: "http://snomed.info/sct", code: "000123" }),
    ).toEqual({ system: "http://snomed.info/sct", code: "000123" });
  });
});

describe("validateReleaseCompareSearch", () => {
  it("defaults when absent", () => {
    expect(validateReleaseCompareSearch({})).toEqual({ from: "", to: "" });
  });

  it("passes through valid values", () => {
    expect(validateReleaseCompareSearch({ from: "R1", to: "R2" })).toEqual({
      from: "R1",
      to: "R2",
    });
  });
});

describe("validateSignInSearch", () => {
  it("omits redirect when absent", () => {
    expect(validateSignInSearch({})).toEqual({});
  });

  it("passes through a redirect path", () => {
    expect(validateSignInSearch({ redirect: "/submissions" })).toEqual({
      redirect: "/submissions",
    });
  });
});
