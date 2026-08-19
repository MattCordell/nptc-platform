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

  // `Number.parseInt` accepts trailing garbage after a numeric prefix and
  // has no ceiling - both would let a malformed page number through as if
  // it were valid.
  it("rejects a page with trailing non-digit characters, rather than parsing its numeric prefix", () => {
    expect(validateCatalogueSearch({ page: "3drop" })).toEqual({
      q: "",
      page: 1,
      sort: "relevance",
    });
  });

  it("rejects a page past the upper bound, rather than accepting an unbounded number", () => {
    expect(validateCatalogueSearch({ page: "99999999999999999999" })).toEqual({
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

  it("passes through an internal redirect path", () => {
    expect(validateSignInSearch({ redirect: "/submissions" })).toEqual({
      redirect: "/submissions",
    });
  });

  // #41 reads `redirect` to send a signed-in user back where they were.
  // Each of these would instead send them off-site (an open redirect) if
  // accepted: a full external URL, a protocol-relative URL (host-relative,
  // despite the leading `/`), and a backslash variant some browsers
  // normalise into a host-relative URL.
  it.each([
    "https://evil.example/",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "evil.example",
  ])("drops a non-internal redirect target: %s", (redirect) => {
    expect(validateSignInSearch({ redirect })).toEqual({});
  });
});

describe("every validator is idempotent", () => {
  // TanStack Router calls validateSearch more than once per navigation, and
  // a later call passes the validator's own previously-validated output back
  // in as input (see the detailed comment on `validateCatalogueSearch`'s own
  // idempotency test above, which is the regression test for the bug this
  // generalises). ADR-0020 makes idempotency a rule for every validator this
  // file defines, not just the one that broke - enforce it as a test here
  // rather than leaving it as documentation a fifth validator could miss.
  const cases: Array<[string, (search: Record<string, unknown>) => unknown]> = [
    ["validateCatalogueSearch", validateCatalogueSearch],
    ["validateLookupSearch", validateLookupSearch],
    ["validateReleaseCompareSearch", validateReleaseCompareSearch],
    ["validateSignInSearch", validateSignInSearch],
  ];

  it.each(cases)(
    "%s is idempotent on its own (empty-input) output",
    (_name, validate) => {
      const once = validate({});
      const twice = validate(once as Record<string, unknown>);
      expect(twice).toEqual(once);
    },
  );
});
