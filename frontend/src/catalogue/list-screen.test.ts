import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { emptyStateText, resultAnnouncement, useCurrentPage } from "./list-screen.ts";

type Query = { data: string | undefined; isPlaceholderData: boolean };

function render(initial: { query: Query; population: string }) {
  return renderHook(({ query, population }) => useCurrentPage(query, population), {
    initialProps: initial,
  });
}

describe("useCurrentPage", () => {
  it("returns settled data", () => {
    const { result } = render({
      query: { data: "page", isPlaceholderData: false },
      population: "browse:",
    });

    expect(result.current).toBe("page");
  });

  it("returns a placeholder while the same population loads its next page", () => {
    const { result, rerender } = render({
      query: { data: "page 1", isPlaceholderData: false },
      population: "browse:",
    });

    rerender({
      query: { data: "page 1", isPlaceholderData: true },
      population: "browse:",
    });

    expect(result.current).toBe("page 1");
  });

  it("hides a placeholder that answers another mode or query", () => {
    const { result, rerender } = render({
      query: { data: "browse page", isPlaceholderData: false },
      population: "browse:",
    });

    rerender({
      query: { data: "browse page", isPlaceholderData: true },
      population: "search:glucose",
    });

    expect(result.current).toBeUndefined();
  });

  it("returns nothing before any data has arrived", () => {
    const { result } = render({
      query: { data: undefined, isPlaceholderData: false },
      population: "browse:",
    });

    expect(result.current).toBeUndefined();
  });
});

describe("resultAnnouncement", () => {
  it("says there are no results", () => {
    expect(resultAnnouncement(0, false)).toBe("No results.");
  });

  it("counts results and says whether more follow", () => {
    expect(resultAnnouncement(1, false)).toBe("1 result on this page.");
    expect(resultAnnouncement(2, true)).toBe(
      "2 results on this page. More results are on the next page.",
    );
  });
});

describe("emptyStateText", () => {
  const base = {
    mode: "browse" as const,
    q: "",
    hasFilters: false,
    hasCursor: false,
    nothingYet: "Nothing yet.",
  };

  it("uses the screen's own wording for an empty, unfiltered catalogue", () => {
    expect(emptyStateText(base)).toBe("Nothing yet.");
  });

  it("says there are no more entries past the first page", () => {
    expect(emptyStateText({ ...base, hasCursor: true })).toBe(
      "No more catalogue entries.",
    );
  });

  it("names the filters and the query when they hide every entry", () => {
    expect(emptyStateText({ ...base, hasFilters: true })).toBe(
      "No catalogue entries match these filters.",
    );
    expect(emptyStateText({ ...base, mode: "search", q: "glucose" })).toBe(
      'No catalogue entries match "glucose".',
    );
    expect(
      emptyStateText({ ...base, mode: "search", q: "glucose", hasFilters: true }),
    ).toBe('No catalogue entries match "glucose" with these filters.');
  });
});
