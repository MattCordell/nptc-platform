import { describe, expect, it } from "vitest";

import {
  activeFilterEntries,
  changeSort,
  clearAllFilters,
  filterSelections,
  toggleFilterValue,
  validateAdminCatalogueSearch,
  validateAuditSearch,
  validateCatalogueSearch,
  validateLookupSearch,
  validatePropertyListSearch,
  validateReleaseCompareSearch,
  validateSignInSearch,
  type AdminCatalogueSearch,
} from "./search-params.ts";

describe("validateAuditSearch", () => {
  it("defaults to no filters and no cursor", () => {
    expect(validateAuditSearch({})).toEqual({});
  });

  it("keeps every filter and the cursor as strings, trimmed", () => {
    expect(
      validateAuditSearch({
        actor: " 3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8 ",
        entity_type: "catalogue_entry",
        entity_id: "NPTC-000001",
        action: "catalogue_entry.updated",
        from: "2026-10-01",
        to: "2026-10-07",
        before: "1042",
      }),
    ).toEqual({
      actor: "3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8",
      entity_type: "catalogue_entry",
      entity_id: "NPTC-000001",
      action: "catalogue_entry.updated",
      from: "2026-10-01",
      to: "2026-10-07",
      before: "1042",
    });
  });

  it("drops blank and non-string values, and keys it does not know", () => {
    expect(
      validateAuditSearch({ actor: "  ", action: ["a", "b"], limit: "5", before: 12 }),
    ).toEqual({});
  });

  it("does not drop an invalid actor, so the page can say what is wrong", () => {
    expect(validateAuditSearch({ actor: "alice" })).toEqual({ actor: "alice" });
  });
});

describe("validatePropertyListSearch", () => {
  it("hides deprecated properties by default", () => {
    expect(validatePropertyListSearch({})).toEqual({});
  });

  it("keeps only the exact value that reveals them", () => {
    expect(validatePropertyListSearch({ deprecated: "show" })).toEqual({
      deprecated: "show",
    });
    expect(validatePropertyListSearch({ deprecated: "true" })).toEqual({});
    expect(validatePropertyListSearch({ deprecated: ["show", "show"] })).toEqual({});
  });
});

describe("validateCatalogueSearch", () => {
  it("defaults to an empty query with no cursor or filters", () => {
    expect(validateCatalogueSearch({})).toEqual({ q: "" });
  });

  it("passes through q, the cursor and every filter value", () => {
    expect(
      validateCatalogueSearch({
        q: "glucose",
        after: "0.5:abc:NPTC-000123",
        "filter.discipline": ["chem", "haem"],
      }),
    ).toEqual({
      q: "glucose",
      after: "0.5:abc:NPTC-000123",
      "filter.discipline": ["chem", "haem"],
    });
  });

  it("normalises a single filter value to an array", () => {
    expect(validateCatalogueSearch({ "filter.discipline": "chem" })).toEqual({
      q: "",
      "filter.discipline": ["chem"],
    });
  });

  // The public endpoints offer neither: a stale link carrying them must not
  // end up sending them, or claiming an order the results are not in.
  it("drops the page and sort keys an older link may carry", () => {
    expect(validateCatalogueSearch({ q: "glucose", page: "3", sort: "code" })).toEqual({
      q: "glucose",
    });
  });

  // FR-06: a facet value can be a SNOMED CT code; it must stay a string.
  it("keeps a numeric-looking filter value as a string", () => {
    const result = validateCatalogueSearch({ "filter.specimen": "119297000" });
    expect(result["filter.specimen"]).toEqual(["119297000"]);
  });

  it("is idempotent - validating its own output reproduces it", () => {
    const once = validateCatalogueSearch({
      q: "glucose",
      after: "NPTC-000123",
      "filter.discipline": "chem",
    });
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

    expect(changeSort(search, "updated_at")).toEqual({
      q: "glucose",
      sort: "updated_at",
    });
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
  // in as input. ADR-0020 makes idempotency a rule for every validator this
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
