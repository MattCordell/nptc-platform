import { describe, expect, it } from "vitest";

import {
  activeFilterEntries,
  changeSort,
  clearAllFilters,
  filterSelections,
  toggleFilterValue,
  validateAdminCatalogueSearch,
  validateCatalogueSearch,
  validateLookupSearch,
  validateReleaseCompareSearch,
  validateSignInSearch,
  type AdminCatalogueSearch,
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

describe("validateAdminCatalogueSearch", () => {
  it("defaults to an empty q with no after and no filters", () => {
    expect(validateAdminCatalogueSearch({})).toEqual({ q: "" });
  });

  it("passes through q and after unchanged", () => {
    expect(validateAdminCatalogueSearch({ q: "glucose", after: "NPTC-000123" })).toEqual({
      q: "glucose",
      after: "NPTC-000123",
    });
  });

  // `parseSearch` (router.tsx) gives a bare string for a filter that appears
  // exactly once in the URL - this must still normalise to a one-element
  // array, matching the shape a repeated value would arrive as.
  it("normalises a single-value filter to a one-element array", () => {
    expect(validateAdminCatalogueSearch({ "filter.status": "draft" })).toEqual({
      q: "",
      "filter.status": ["draft"],
    });
  });

  it("keeps a repeated filter as an array of every value", () => {
    expect(
      validateAdminCatalogueSearch({ "filter.discipline": ["chemistry", "haematology"] }),
    ).toEqual({ q: "", "filter.discipline": ["chemistry", "haematology"] });
  });

  it("keeps two different filters as two separate keys", () => {
    expect(
      validateAdminCatalogueSearch({
        "filter.status": "draft",
        "filter.discipline": "chemistry",
      }),
    ).toEqual({ q: "", "filter.status": ["draft"], "filter.discipline": ["chemistry"] });
  });

  it("drops a filter whose only value is blank, rather than sending an empty selection", () => {
    expect(validateAdminCatalogueSearch({ "filter.status": "" })).toEqual({ q: "" });
  });

  it("ignores a query parameter that does not carry the filter. prefix", () => {
    expect(validateAdminCatalogueSearch({ q: "glucose", page: "3" })).toEqual({
      q: "glucose",
    });
  });

  it("is idempotent - validating its own output reproduces it", () => {
    const once = validateAdminCatalogueSearch({
      q: "glucose",
      after: "NPTC-000123",
      "filter.status": ["draft", "active"],
    });
    const twice = validateAdminCatalogueSearch(
      once as unknown as Record<string, unknown>,
    );
    expect(twice).toEqual(once);
  });

  // Issue #287. `sort` is optional, matching `after`'s own precedent: the
  // default (`business_key`) is not worth always writing into the URL,
  // unlike `CatalogueSearch.sort` above, which has no backend default to
  // fall back to.
  it("omits sort from the output when it is business_key, the default", () => {
    expect(validateAdminCatalogueSearch({ sort: "business_key" })).toEqual({ q: "" });
  });

  it("keeps a recognised, non-default sort", () => {
    expect(validateAdminCatalogueSearch({ sort: "updated_at" })).toEqual({
      q: "",
      sort: "updated_at",
    });
  });

  it("degrades an unrecognised sort to the default rather than throwing", () => {
    expect(validateAdminCatalogueSearch({ sort: "nonsense" })).toEqual({ q: "" });
  });
});

describe("filterSelections", () => {
  it("returns an empty record when there are no filters", () => {
    expect(filterSelections({ q: "glucose" })).toEqual({});
  });

  it("strips the filter. prefix from each key", () => {
    const search: AdminCatalogueSearch = {
      q: "",
      "filter.status": ["draft"],
      "filter.discipline": ["chemistry", "haematology"],
    };

    expect(filterSelections(search)).toEqual({
      status: ["draft"],
      discipline: ["chemistry", "haematology"],
    });
  });
});

describe("toggleFilterValue", () => {
  it("adds a value to a facet with no existing selection", () => {
    const search: AdminCatalogueSearch = { q: "glucose" };

    expect(toggleFilterValue(search, "status", "draft")).toEqual({
      q: "glucose",
      "filter.status": ["draft"],
    });
  });

  it("adds a second value to an existing selection", () => {
    const search: AdminCatalogueSearch = { q: "", "filter.status": ["draft"] };

    expect(toggleFilterValue(search, "status", "active")).toEqual({
      q: "",
      "filter.status": ["draft", "active"],
    });
  });

  it("removes a value already selected, rather than adding a duplicate", () => {
    const search: AdminCatalogueSearch = { q: "", "filter.status": ["draft", "active"] };

    expect(toggleFilterValue(search, "status", "draft")).toEqual({
      q: "",
      "filter.status": ["active"],
    });
  });

  it("drops the parameter entirely once its last value is removed", () => {
    const search: AdminCatalogueSearch = { q: "", "filter.status": ["draft"] };

    const result = toggleFilterValue(search, "status", "draft");

    expect(result).toEqual({ q: "" });
    expect("filter.status" in result).toBe(false);
  });

  it("leaves other facets untouched", () => {
    const search: AdminCatalogueSearch = {
      q: "",
      "filter.status": ["draft"],
      "filter.discipline": ["chemistry"],
    };

    expect(toggleFilterValue(search, "status", "active")).toEqual({
      q: "",
      "filter.status": ["draft", "active"],
      "filter.discipline": ["chemistry"],
    });
  });

  // ADR-0024: a cursor is only meaningful against the request that produced
  // it, and changing the filter set changes the population being paged.
  it("drops the after cursor, since the population it was paging over has changed", () => {
    const search: AdminCatalogueSearch = { q: "", after: "NPTC-000123" };

    const result = toggleFilterValue(search, "status", "draft");

    expect("after" in result).toBe(false);
  });
});

// PR #285 review finding 1: an escape hatch for a `filter.*` the panel
// cannot render a control for (not `concept_picker`, or since dropped from
// the registry) - both `activeFilterEntries` and `clearAllFilters` must work
// from the raw search object alone, never from the panel's own recognised
// facets, or they would be exactly as blind to the unrecognised key as the
// panel is.
describe("activeFilterEntries", () => {
  it("returns nothing when there are no filters", () => {
    expect(activeFilterEntries({ q: "glucose" })).toEqual([]);
  });

  it("flattens one entry per selected value, across every facet", () => {
    const search: AdminCatalogueSearch = {
      q: "",
      "filter.status": ["draft", "active"],
      "filter.discipline": ["chemistry"],
    };

    expect(activeFilterEntries(search)).toEqual([
      { facetKey: "status", value: "draft" },
      { facetKey: "status", value: "active" },
      { facetKey: "discipline", value: "chemistry" },
    ]);
  });

  // The exact shape of PR #285 review finding 1's first scenario: a
  // `filter.*` key the panel never renders a control for still round-trips
  // here, since this reads the raw search object rather than the panel's
  // own definition-derived facet list.
  it("includes a filter key the caller does not recognise as a known facet", () => {
    const search: AdminCatalogueSearch = { q: "", "filter.volume_ml": ["5"] };

    expect(activeFilterEntries(search)).toEqual([{ facetKey: "volume_ml", value: "5" }]);
  });
});

describe("clearAllFilters", () => {
  it("drops every filter and the after cursor, keeping q", () => {
    const search: AdminCatalogueSearch = {
      q: "glucose",
      after: "NPTC-000123",
      "filter.status": ["draft"],
      "filter.discipline": ["chemistry"],
    };

    expect(clearAllFilters(search)).toEqual({ q: "glucose" });
  });

  it("is a no-op on a search with no filters", () => {
    expect(clearAllFilters({ q: "glucose" })).toEqual({ q: "glucose" });
  });

  // Issue #287: clearing filters does not invalidate an ordering the way
  // changing sort itself does, so sort survives it unlike after.
  it("keeps sort while dropping the after cursor", () => {
    const search: AdminCatalogueSearch = {
      q: "glucose",
      after: "NPTC-000123",
      sort: "updated_at",
      "filter.status": ["draft"],
    };

    expect(clearAllFilters(search)).toEqual({ q: "glucose", sort: "updated_at" });
  });
});

describe("changeSort", () => {
  it("sets sort and drops the after cursor", () => {
    const search: AdminCatalogueSearch = { q: "glucose", after: "NPTC-000123" };

    expect(changeSort(search, "updated_at")).toEqual({ q: "glucose", sort: "updated_at" });
  });

  it("omits sort when changed back to business_key, the default", () => {
    const search: AdminCatalogueSearch = {
      q: "glucose",
      after: "NPTC-000123",
      sort: "updated_at",
    };

    expect(changeSort(search, "business_key")).toEqual({ q: "glucose" });
  });

  it("leaves filters untouched", () => {
    const search: AdminCatalogueSearch = { q: "", "filter.status": ["draft"] };

    expect(changeSort(search, "status")).toEqual({
      q: "",
      sort: "status",
      "filter.status": ["draft"],
    });
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
    ["validateAdminCatalogueSearch", validateAdminCatalogueSearch],
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
