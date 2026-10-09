import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import {
  useCatalogueFacets,
  useCatalogueSearch,
  useEntriesList,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { FilterBar } from "../components/filter-bar.tsx";
import type { ActiveFilter } from "../components/filter-bar.tsx";
import { FindingIndicator } from "../components/finding-indicator.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { MultiSelectCombobox } from "../components/multi-select-combobox.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Pagination } from "../components/pagination.tsx";
import { SearchInput } from "../components/search-input.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import {
  activeFilterEntries,
  clearAllFilters,
  filterSelections,
  toggleFilterValue,
} from "../router/search-params.ts";

/**
 * The public catalogue browse and search screen (FR-14..18, NFR-31).
 *
 * An empty `q` browses `GET /catalogue/entries`; a non-empty one searches
 * `GET /catalogue/search`. Discipline and Specimen are offered as comboboxes
 * from the first load, browsing or searching. An anonymous caller cannot read
 * the property registry, so their options come from a facets request
 * (`useCatalogueFacets`) that is separate from the results request: paging
 * changes the results and leaves the counts alone. Any other filter in the URL
 * still applies and shows as a removable chip, with no control of its own.
 *
 * Paging is keyset (ADR-0024): no page number and no total exist, so the
 * screen never states one.
 */

const ROUTE_ID = "/catalogue/" as const;

type Row = components["schemas"]["EntrySummary"] | components["schemas"]["SearchHit"];
type Facet = components["schemas"]["Facet"];

const PAGE_SIZE = 50;

/** The facets with a combobox, in the order they appear. */
const COMBOBOX_FACET_KEYS = ["discipline", "specimen"] as const;

/** The API refuses a selection of more values than this in one filter
 * (`FILTER_VALUE_CAP`, ADR-0032), so the comboboxes stop at it too. */
const MAX_VALUES_PER_FILTER = 50;

const STALE_DATA_WARNING =
  "The catalogue could not be refreshed just now, so these results may be out of date.";

const FACETS_UNAVAILABLE =
  "Filters are unavailable just now. You can still search, and the results are not affected.";

const LOAD_FAILURE =
  "The catalogue could not be loaded. Try again in a moment, or change the search.";

function resultAnnouncement(count: number, hasNext: boolean): string {
  if (count === 0) {
    return "No results.";
  }
  const shown = `${count} result${count === 1 ? "" : "s"} on this page.`;
  return hasNext ? `${shown} More results are on the next page.` : shown;
}

function emptyStateText(
  mode: "browse" | "search",
  q: string,
  hasFilters: boolean,
  hasCursor: boolean,
): string {
  if (mode === "search") {
    return hasFilters
      ? `No catalogue entries match "${q}" with these filters.`
      : `No catalogue entries match "${q}".`;
  }
  if (hasFilters) {
    return "No catalogue entries match these filters.";
  }
  return hasCursor
    ? "No more catalogue entries."
    : "The catalogue has no published entries yet.";
}

export function CatalogueSearchPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_ID });
  const { message, politeness, announce } = useAnnounce();

  const filters = useMemo(() => filterSelections(search), [search]);
  const activeFilters = useMemo(() => activeFilterEntries(search), [search]);
  const mode: "browse" | "search" = search.q.trim().length > 0 ? "search" : "browse";

  const listQuery = useEntriesList({
    limit: PAGE_SIZE,
    after: search.after,
    filters,
    enabled: mode === "browse",
    keepPreviousPage: true,
  });
  const searchQuery = useCatalogueSearch({
    q: search.q,
    limit: PAGE_SIZE,
    after: search.after,
    filters,
    enabled: mode === "search",
    keepPreviousPage: true,
  });
  const active = mode === "browse" ? listQuery : searchQuery;

  // Placeholder data is the previous page, kept so a focused paging control
  // or facet pill stays mounted while the next page loads. It is only that
  // when it answers the same mode and query; otherwise it is another
  // search's results, and is neither shown nor announced.
  const population = `${mode}:${search.q}`;
  const [freshPopulation, setFreshPopulation] = useState<string | null>(null);
  if (active.data && !active.isPlaceholderData && freshPopulation !== population) {
    setFreshPopulation(population);
  }
  const data =
    active.isPlaceholderData && freshPopulation !== population ? undefined : active.data;

  const items: Row[] = data?.items ?? [];
  const nextCursor = data?.next_cursor ?? null;

  const facetsQuery = useCatalogueFacets({ q: search.q, filters });
  const facets: readonly Facet[] = useMemo(
    () => facetsQuery.data ?? [],
    [facetsQuery.data],
  );
  const comboboxFacets = useMemo(
    () =>
      COMBOBOX_FACET_KEYS.flatMap((key) => {
        const facet = facets.find((candidate) => candidate.key === key);
        return facet !== undefined && facet.facetable && facet.buckets.length > 0
          ? [facet]
          : [];
      }),
    [facets],
  );

  // The draft mirrors `search.q`, so Back, Forward or a pasted link shows its
  // query in the box, yet stays editable between keystrokes.
  const [queryDraft, setQueryDraft] = useState(search.q);
  const [committedQ, setCommittedQ] = useState(search.q);
  if (search.q !== committedQ) {
    setCommittedQ(search.q);
    setQueryDraft(search.q);
  }

  // The cursors of pages already visited, so "Previous page" can return to
  // one. The stack survives a change of `after` only when that change is the
  // Next or Previous click that set `target`; anything else empties it.
  const [paging, setPaging] = useState<{
    stack: (string | undefined)[];
    seen: string | undefined;
    target: { after: string | undefined } | null;
  }>({ stack: [], seen: search.after, target: null });
  if (search.after !== paging.seen) {
    setPaging({
      stack:
        paging.target !== null && paging.target.after === search.after
          ? paging.stack
          : [],
      seen: search.after,
      target: null,
    });
  }

  const labelsByFacet = useMemo(() => {
    const map = new Map<string, { label: string; values: Map<string, string> }>();
    for (const facet of facets) {
      map.set(facet.key, {
        label: facet.label,
        values: new Map(facet.buckets.map((bucket) => [bucket.value, bucket.label])),
      });
    }
    return map;
  }, [facets]);

  // The chip key only has to be unique; removal maps it back through
  // `filterByChipKey`, not by parsing it.
  const { chips, filterByChipKey } = useMemo(() => {
    const byKey = new Map<string, { facetKey: string; value: string }>();
    const built: ActiveFilter[] = activeFilters.map(({ facetKey, value }) => {
      const key = JSON.stringify([facetKey, value]);
      byKey.set(key, { facetKey, value });
      const known = labelsByFacet.get(facetKey);
      return {
        key,
        facetLabel: known?.label ?? facetKey,
        valueLabel: known?.values.get(value) ?? value,
      };
    });
    return { chips: built, filterByChipKey: byKey };
  }, [activeFilters, labelsByFacet]);

  function handleSearchSubmit(trimmed: string) {
    void navigate({ search: (prev) => ({ ...prev, q: trimmed, after: undefined }) });
  }

  function handleFilterToggle(facetKey: string, value: string) {
    void navigate({ search: (prev) => toggleFilterValue(prev, facetKey, value) });
  }

  function handleChipRemove(chipKey: string) {
    const filter = filterByChipKey.get(chipKey);
    if (filter) {
      handleFilterToggle(filter.facetKey, filter.value);
    }
  }

  function handleClearAllFilters() {
    void navigate({ search: (prev) => clearAllFilters(prev) });
  }

  function handleNextPage() {
    // A placeholder's `next_cursor` points at the page already loading, so
    // acting on it would push a duplicate onto the Previous stack.
    if (nextCursor !== null && !active.isPlaceholderData) {
      setPaging({
        ...paging,
        stack: [...paging.stack, search.after],
        target: { after: nextCursor },
      });
      void navigate({ search: (prev) => ({ ...prev, after: nextCursor }) });
    }
  }

  function handlePreviousPage() {
    const previousAfter = paging.stack[paging.stack.length - 1];
    setPaging({
      ...paging,
      stack: paging.stack.slice(0, -1),
      target: { after: previousAfter },
    });
    void navigate({ search: (prev) => ({ ...prev, after: previousAfter }) });
  }

  const staleData = active.isError && data !== undefined;
  const hardFailure = active.isError && data === undefined;
  const hardFailureMessage = refusalDetail(active.error) ?? LOAD_FAILURE;

  // `data` keeps its identity across a refetch that returns the same page
  // (structural sharing), so this speaks once per new result set. A
  // placeholder is never announced: it is the page being replaced.
  const resultMessage =
    data && !active.isError && !active.isPlaceholderData
      ? resultAnnouncement(data.items.length, data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(resultMessage);
    }
  }, [data, resultMessage, announce]);

  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  useEffect(() => {
    if (hardFailure) {
      announce(hardFailureMessage);
    }
  }, [hardFailure, hardFailureMessage, announce]);

  // A refused filter fails both requests, and the results failure is the one
  // to say, so the filters note is only for a failure of its own.
  const facetsFailed =
    facetsQuery.isError && facetsQuery.data === undefined && !hardFailure;
  useEffect(() => {
    if (facetsFailed) {
      announce(FACETS_UNAVAILABLE);
    }
  }, [facetsFailed, announce]);

  const emptyState = (
    <div className="flex flex-col items-start gap-2">
      <p className="m-0">
        {emptyStateText(
          mode,
          search.q,
          activeFilters.length > 0,
          search.after !== undefined,
        )}
      </p>
      {activeFilters.length > 0 ? (
        <Button type="button" variant="secondary" onClick={handleClearAllFilters}>
          Clear all filters
        </Button>
      ) : null}
    </div>
  );

  return (
    <section aria-labelledby="catalogue-search-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="flex flex-col gap-4 py-6">
        <PageHeader id="catalogue-search-heading" title="Search the catalogue" />

        <SearchInput
          id="catalogue-search-query"
          label="Search term or SNOMED CT code"
          value={queryDraft}
          onValueChange={setQueryDraft}
          onSubmit={handleSearchSubmit}
        />

        {comboboxFacets.length > 0 ? (
          <div className="flex flex-wrap items-start gap-4">
            {comboboxFacets.map((facet) => (
              <MultiSelectCombobox
                key={facet.key}
                id={`catalogue-facet-${facet.key}`}
                label={facet.label}
                options={facet.buckets.map((bucket) => ({
                  value: bucket.value,
                  label: bucket.label,
                  count: bucket.count,
                }))}
                selected={filters[facet.key] ?? []}
                onToggle={(value) => handleFilterToggle(facet.key, value)}
                maxSelected={MAX_VALUES_PER_FILTER}
              />
            ))}
          </div>
        ) : null}

        {facetsFailed ? (
          <p className="m-0 text-sm text-[var(--color-text-muted)]">
            {FACETS_UNAVAILABLE}
          </p>
        ) : null}

        {facetsQuery.isPending ? (
          <p className="m-0 text-sm text-[var(--color-text-muted)]">Loading filters…</p>
        ) : null}

        {/* Outside the `data` gate: removing a filter is the way out
            of a refused request, so it stays reachable on failure. */}
        <FilterBar
          activeFilters={chips}
          onRemove={handleChipRemove}
          onClearAll={handleClearAllFilters}
        />

        {(active.isPending || active.isPlaceholderData) && (
          <p className="m-0">Loading catalogue entries…</p>
        )}

        {hardFailure && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p className="m-0">{STALE_DATA_WARNING}</p>}

        {data && (
          <>
            {/* Scrolls on its own at narrow widths rather than widening the
                page. Its row links are what a keyboard user scrolls it by. */}
            <div data-testid="results-scroll" className="overflow-x-auto">
              <DataTable
                caption={mode === "search" ? "Search results" : "Catalogue entries"}
                columns={[
                  {
                    key: "preferred_term",
                    header: "Requesting term",
                    isRowHeader: true,
                    render: (row: Row) => (
                      <span className="inline-flex flex-wrap items-center gap-2">
                        <Link
                          to="/catalogue/$businessKey"
                          params={{ businessKey: row.business_key }}
                          className="text-[var(--color-accent)] hover:underline"
                        >
                          {row.preferred_term}
                        </Link>
                        {row.has_open_finding ? <FindingIndicator /> : null}
                      </span>
                    ),
                  },
                  {
                    key: "disciplines",
                    header: "Discipline",
                    render: (row: Row) =>
                      row.disciplines.length > 0 ? (
                        row.disciplines.join(", ")
                      ) : (
                        <span className="text-[var(--color-text-muted)]">
                          None recorded
                        </span>
                      ),
                  },
                  {
                    key: "specimens",
                    header: "Specimen",
                    render: (row: Row) =>
                      row.specimens.length > 0 ? (
                        row.specimens.join(", ")
                      ) : (
                        <span className="text-[var(--color-text-muted)]">
                          None recorded
                        </span>
                      ),
                  },
                  {
                    key: "fsn",
                    header: "SNOMED CT FSN",
                    render: (row: Row) =>
                      row.fsn === null ? (
                        <span className="text-[var(--color-text-muted)]">No code</span>
                      ) : (
                        row.fsn
                      ),
                  },
                ]}
                rows={items}
                getRowKey={(row) => row.business_key}
                emptyState={emptyState}
              />
            </div>

            {(items.length > 0 || paging.stack.length > 0) && (
              <Pagination
                hasNext={nextCursor !== null}
                onNext={handleNextPage}
                onPrevious={paging.stack.length > 0 ? handlePreviousPage : undefined}
                className="self-start"
              />
            )}
          </>
        )}
      </PageContainer>
    </section>
  );
}
