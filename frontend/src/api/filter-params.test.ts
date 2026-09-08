import { describe, expect, it } from "vitest";

import { filterQueryParams } from "./filter-params.ts";

describe("filterQueryParams", () => {
  it("returns an empty object for no selections", () => {
    expect(filterQueryParams({})).toEqual({});
  });

  it("prefixes each facet key with filter.", () => {
    expect(filterQueryParams({ status: ["draft"] })).toEqual({ "filter.status": ["draft"] });
  });

  it("keeps every selected value for a facet", () => {
    expect(filterQueryParams({ discipline: ["chemistry", "haematology"] })).toEqual({
      "filter.discipline": ["chemistry", "haematology"],
    });
  });

  it("builds one prefixed key per facet", () => {
    expect(
      filterQueryParams({ status: ["draft"], discipline: ["chemistry"] }),
    ).toEqual({ "filter.status": ["draft"], "filter.discipline": ["chemistry"] });
  });

  it("omits a facet with no selected values, rather than sending an empty filter", () => {
    expect(filterQueryParams({ status: [] })).toEqual({});
  });
});
