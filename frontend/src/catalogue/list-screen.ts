import { useState } from "react";

/**
 * What the two catalogue list screens (`/catalogue` and `/admin/catalogue`)
 * share, so a change to one cannot silently miss the other.
 */

/** The API refuses a selection of more values than this in one filter
 * (`FILTER_VALUE_CAP`, ADR-0032), so the comboboxes stop at it too. */
export const MAX_VALUES_PER_FILTER = 50;

export function resultAnnouncement(count: number, hasNext: boolean): string {
  if (count === 0) {
    return "No results.";
  }
  const shown = `${count} result${count === 1 ? "" : "s"} on this page.`;
  return hasNext ? `${shown} More results are on the next page.` : shown;
}

export function emptyStateText(options: {
  mode: "browse" | "search";
  q: string;
  hasFilters: boolean;
  hasCursor: boolean;
  /** What an unfiltered first page with nothing on it means on this screen. */
  nothingYet: string;
}): string {
  const { mode, q, hasFilters, hasCursor, nothingYet } = options;
  if (mode === "search") {
    return hasFilters
      ? `No catalogue entries match "${q}" with these filters.`
      : `No catalogue entries match "${q}".`;
  }
  if (hasFilters) {
    return "No catalogue entries match these filters.";
  }
  return hasCursor ? "No more catalogue entries." : nothingYet;
}

/**
 * The page a list screen should show for its current query. A query kept on
 * screen with `keepPreviousData` returns the previous page while the next one
 * loads, so a focused paging control stays mounted. That placeholder counts
 * only when it answers the same `population` (mode and query text); otherwise
 * it is another search's results, and is neither shown nor announced.
 */
export function useCurrentPage<T>(
  query: { data: T | undefined; isPlaceholderData: boolean },
  population: string,
): T | undefined {
  const [freshPopulation, setFreshPopulation] = useState<string | null>(null);
  if (query.data && !query.isPlaceholderData && freshPopulation !== population) {
    setFreshPopulation(population);
  }
  return query.isPlaceholderData && freshPopulation !== population
    ? undefined
    : query.data;
}
