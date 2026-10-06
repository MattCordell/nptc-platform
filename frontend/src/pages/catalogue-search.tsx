import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useCatalogueSearch, useEntriesList } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { FilterBar } from "../components/filter-bar.tsx";
import type {
  ActiveFilter,
  FilterDropdown,
  FilterToggleGroup,
} from "../components/filter-bar.tsx";
import { LiveRegion } from "../components/live-region.tsx";
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
 * `GET /catalogue/search`. Only the search response carries facets, and an
 * anonymous caller cannot read the property registry, so the facet controls
 * appear only while searching. Filters already in the URL still apply while
 * browsing, and show as removable chips under their raw key and value.
 *
 * Paging is keyset (ADR-0024): no page number and no total exist, so the
 * screen never states one.
 */

const ROUTE_ID = "/catalogue/" as const;

type Row = components["schemas"]["EntrySummary"] | components["schemas"]["SearchHit"];
type Facet = components["schemas"]["Facet"];

const PAGE_SIZE = 50;

/** Facets with at most this many buckets render as toggle pills; more as a
 * dropdown, which would otherwise be a wall of buttons. */
const MAX_TOGGLE_BUCKETS = 8;

const STALE_DATA_WARNING =
  "The catalogue could not be refreshed just now, so these results may be out of date.";

const LOAD_FAILURE =
  "The catalogue could not be loaded. Try again in a moment, or change the search.";

function resultAnnouncement(count: number, hasNext: boolean): string {
  if (count === 0) {
    return "No results.";
  }
  const shown = `${count} result${count === 1 ? "" : "s"} on this page.`;
  return hasNext ? `${shown} More results are on the next page.` : shown;
}

/**
 * The facets worth a control. `status` is left out: the public surface serves
 * `active` entries only, so it is always one bucket. A facet that cannot be
 * grouped has no buckets to offer.
 */
function offeredFacets(facets: readonly Facet[]): Facet[] {
  return facets.filter(
    (facet) => facet.key !== "status" && facet.facetable && facet.buckets.length > 0,
  );
}

function facetControls(
  facets: readonly Facet[],
  selections: Record<string, string[]>,
  onToggle: (facetKey: string, value: string) => void,
): { toggleGroups: FilterToggleGroup[]; dropdowns: FilterDropdown[] } {
  const toggleGroups: FilterToggleGroup[] = [];
  const dropdowns: FilterDropdown[] = [];
  for (const facet of facets) {
    const selected = selections[facet.key] ?? [];
    const options = facet.buckets.map((bucket) => ({
      value: bucket.value,
      label: bucket.label,
      count: bucket.count,
    }));
    if (options.length <= MAX_TOGGLE_BUCKETS) {
      toggleGroups.push({
        label: facet.label,
        options,
        selected,
        onToggle: (value) => onToggle(facet.key, value),
      });
    } else {
      // Always reset to the placeholder: a pick adds a chip, and the chip row
      // is where a selected value is shown and removed.
      dropdowns.push({
        id: `catalogue-facet-${facet.key}`,
        label: facet.label,
        options: options.filter((option) => !selected.includes(option.value)),
        value: "",
        placeholder: "Add a value",
        onChange: (value) => {
          if (value !== "") {
            onToggle(facet.key, value);
          }
        },
      });
    }
  }
  return { toggleGroups, dropdowns };
}

function FindingIndicator({ open }: { open: boolean }) {
  if (!open) {
    return <span className="text-[var(--color-text-muted)]">None</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-[var(--color-danger-surface)] px-2 py-0.5 text-xs font-medium text-[var(--color-danger)]">
      <span aria-hidden="true">!</span>
      Open finding
    </span>
  );
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
  });
  const searchQuery = useCatalogueSearch({
    q: search.q,
    limit: PAGE_SIZE,
    after: search.after,
    filters,
    enabled: mode === "search",
  });
  const active = mode === "browse" ? listQuery : searchQuery;
  const items: Row[] = active.data?.items ?? [];
  const nextCursor = active.data?.next_cursor ?? null;
  const facets = useMemo(
    () => (mode === "search" ? offeredFacets(searchQuery.data?.facets ?? []) : []),
    [mode, searchQuery.data],
  );
  const truncatedFacets = facets.filter((facet) => facet.truncated);

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
    for (const facet of searchQuery.data?.facets ?? []) {
      map.set(facet.key, {
        label: facet.label,
        values: new Map(facet.buckets.map((bucket) => [bucket.value, bucket.label])),
      });
    }
    return map;
  }, [searchQuery.data]);

  // The chip key only has to be unique; removal maps it back through
  // `filterByChipKey`, not by parsing it.
  const { chips, filterByChipKey } = useMemo(() => {
    const byKey = new Map<string, { facetKey: string; value: string }>();
    const built: ActiveFilter[] = activeFilters.map(({ facetKey, value }) => {
      const key = JSON.stringify([facetKey, value]);
      byKey.set(key, { facetKey, value });
      const known = mode === "search" ? labelsByFacet.get(facetKey) : undefined;
      return {
        key,
        facetLabel: known?.label ?? facetKey,
        valueLabel: known?.values.get(value) ?? value,
      };
    });
    return { chips: built, filterByChipKey: byKey };
  }, [activeFilters, labelsByFacet, mode]);

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
    if (nextCursor !== null) {
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

  const staleData = active.isError && active.data !== undefined;
  const hardFailure = active.isError && active.data === undefined;
  const hardFailureMessage = refusalDetail(active.error) ?? LOAD_FAILURE;

  // `active.data` keeps its identity across a refetch that returns the same
  // page (structural sharing), so this speaks once per new result set.
  const resultMessage =
    active.data && !active.isError
      ? resultAnnouncement(active.data.items.length, active.data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(resultMessage);
    }
  }, [active.data, resultMessage, announce]);

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

  const { toggleGroups, dropdowns } = facetControls(facets, filters, handleFilterToggle);

  const emptyState = (
    <div className="flex flex-col items-start gap-2">
      <p className="m-0">
        {mode === "search"
          ? `No catalogue entries match "${search.q}".`
          : "No catalogue entries match these filters."}
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

        {mode === "browse" ? (
          <p className="m-0 text-sm text-[var(--color-text-muted)]">
            Search to filter the results by discipline and other properties.
          </p>
        ) : null}

        {/* Outside the `active.data` gate: removing a filter is the way out
            of a refused request, so it stays reachable on failure. */}
        <FilterBar
          aria-label="Filters"
          toggleGroups={toggleGroups}
          dropdowns={dropdowns}
          activeFilters={chips}
          onRemove={handleChipRemove}
          onClearAll={handleClearAllFilters}
        />

        {truncatedFacets.map((facet) => (
          <p key={facet.key} className="m-0 text-sm text-[var(--color-text-muted)]">
            {facet.label} shows only its most common values. Narrow the search to see
            others.
          </p>
        ))}

        {active.isPending && <p className="m-0">Loading catalogue entries…</p>}

        {hardFailure && (
          <p className="m-0 text-[var(--color-danger)]">
            {hardFailureMessage}
          </p>
        )}

        {staleData && <p className="m-0">{STALE_DATA_WARNING}</p>}

        {active.data && (
          <>
            <DataTable
              caption={mode === "search" ? "Search results" : "Catalogue entries"}
              columns={[
                {
                  key: "preferred_term",
                  header: "Requesting term",
                  isRowHeader: true,
                  render: (row: Row) => (
                    <Link
                      to="/catalogue/$businessKey"
                      params={{ businessKey: row.business_key }}
                      className="text-[var(--color-accent)] hover:underline"
                    >
                      {row.preferred_term}
                    </Link>
                  ),
                },
                {
                  key: "code",
                  header: "SNOMED CT code",
                  render: (row: Row) =>
                    row.code === null ? (
                      <span className="text-[var(--color-text-muted)]">No code</span>
                    ) : (
                      <span className="font-mono">{row.code}</span>
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
                  key: "has_open_finding",
                  header: "Validation",
                  render: (row: Row) => <FindingIndicator open={row.has_open_finding} />,
                },
              ]}
              rows={items}
              getRowKey={(row) => row.business_key}
              emptyState={emptyState}
            />

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
