import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import {
  MAX_RESOLVE_CODES,
  useAdminEntriesList,
  useAdminSearch,
  usePropertyDefinitions,
  usePropertyValueOptionsQueries,
  usePropertyValueResolveQueries,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import {
  STATUS_OPTIONS,
  statusLabelFor,
  statusToneFor,
} from "../catalogue/status-options.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { FilterBar } from "../components/filter-bar.tsx";
import type { ActiveFilter } from "../components/filter-bar.tsx";
import { FindingIndicator } from "../components/finding-indicator.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { MultiSelectCombobox } from "../components/multi-select-combobox.tsx";
import type { MultiSelectOption } from "../components/multi-select-combobox.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Pagination } from "../components/pagination.tsx";
import { SearchInput } from "../components/search-input.tsx";
import { Select } from "../components/select.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { useKeysetPaging } from "../components/use-keyset-paging.ts";
import {
  activeFilterEntries,
  changeSort,
  clearAllFilters,
  filterSelections,
  toggleFilterValue,
} from "../router/search-params.ts";
import type { AdminListingSort } from "../router/search-params.ts";

/**
 * The editor's entry point at `/admin/catalogue/` (FR-14, FR-15, FR-16,
 * FR-36, NFR-31): the layout and behaviour of `/catalogue`, read from the
 * admin routes so drafts, deprecated and withdrawn entries are listed with
 * their status. Selecting a row opens that entry's edit form.
 *
 * An empty `q` browses `GET /catalogue/admin/entries`; a non-empty one
 * searches `GET /catalogue/admin/search`. Both take the same `filter.*`
 * parameters (ADR-0032). Status, Discipline and Specimen are comboboxes. Any
 * other filter in the URL still applies and shows as a removable chip. Admin
 * browse has no facet counts, so the options come from the property registry
 * and carry none.
 *
 * Paging is keyset (ADR-0024): no page number and no total exist.
 */

// TanStack Router keys `useSearch`'s `from` off the route's internal id, which
// includes the pathless `authenticated` layout, while `useNavigate`'s `from`
// is the resolved URL path.
const ROUTE_ID = "/authenticated/admin/catalogue/" as const;
const ROUTE_PATH = "/admin/catalogue/" as const;

type Row =
  components["schemas"]["AdminEntrySummary"] | components["schemas"]["AdminSearchHit"];
type PropertyDefinition = components["schemas"]["PropertyDefinitionResponse"];

const PAGE_SIZE = 50;

/** The coded properties with a combobox, in the order they appear. */
const COMBOBOX_PROPERTY_KEYS = ["discipline", "specimen"] as const;

/** The most values the options route returns in one page (`count` ceiling). A
 * larger page than its default so a multi-hundred-value set stays pickable. */
const OPTIONS_PAGE_SIZE = 200;

/** The API refuses a selection of more values than this in one filter
 * (`FILTER_VALUE_CAP`, ADR-0032), so the comboboxes stop at it too. */
const MAX_VALUES_PER_FILTER = 50;

const SORT_OPTIONS: { value: AdminListingSort; label: string }[] = [
  { value: "business_key", label: "Identifier" },
  { value: "preferred_term", label: "Requesting term" },
  { value: "updated_at", label: "Last changed" },
  { value: "status", label: "Status" },
];

/** Shown while searching: search results are relevance-ranked, so none of
 * `SORT_OPTIONS` describes their order. Never sent to the API. */
const SEARCH_MODE_SORT_VALUE = "relevance";

const STALE_DATA_WARNING =
  "Catalogue entries could not be refreshed just now, so what follows may be out of date.";

const LOAD_FAILURE =
  "Catalogue entries could not be loaded. Try again, or contact an administrator if the problem persists.";

const OPTIONS_UNAVAILABLE =
  "Some filter options could not be loaded just now. You can still search, and the results are not affected.";

function sortLabel(sort: AdminListingSort): string {
  return SORT_OPTIONS.find((option) => option.value === sort)?.label ?? sort;
}

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
  return hasCursor ? "No more catalogue entries." : "The catalogue has no entries yet.";
}

function isOfferedProperty(definition: PropertyDefinition | undefined): boolean {
  return (
    definition !== undefined &&
    definition.filterable &&
    definition.status === "active" &&
    definition.form_control.control === "concept_picker"
  );
}

export function AdminCatalogueListPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_PATH });
  const { message, politeness, announce } = useAnnounce();

  const filters = useMemo(() => filterSelections(search), [search]);
  const mode: "browse" | "search" = search.q.trim().length > 0 ? "search" : "browse";

  // Flattened from the URL rather than from `filters`, so a filter with no
  // control of its own (not offered here, or since dropped from the registry)
  // can still be seen and cleared.
  const activeFilters = useMemo(() => activeFilterEntries(search), [search]);

  // Keyed by every definition, not only the offered ones: a chip must still
  // resolve the label of a deprecated or non-coded property.
  const definitions = usePropertyDefinitions();
  const definitionByKey = useMemo(() => {
    const map = new Map<string, PropertyDefinition>();
    for (const definition of definitions.data?.items ?? []) {
      map.set(definition.key, definition);
    }
    return map;
  }, [definitions.data]);

  const comboboxPropertyKeys = useMemo(
    () =>
      COMBOBOX_PROPERTY_KEYS.filter((key) => isOfferedProperty(definitionByKey.get(key))),
    [definitionByKey],
  );

  // Coded properties whose values must be fetched: the ones with a combobox,
  // and any other coded property holding a selection, so its chip can show a
  // label in place of a code. One request per property, shared by both uses.
  const valueKeys = useMemo(() => {
    const keys = new Set<string>(comboboxPropertyKeys);
    for (const { facetKey } of activeFilters) {
      if (definitionByKey.get(facetKey)?.form_control.control === "concept_picker") {
        keys.add(facetKey);
      }
    }
    return Array.from(keys);
  }, [comboboxPropertyKeys, activeFilters, definitionByKey]);
  const valueOptionsQueries = usePropertyValueOptionsQueries(
    valueKeys.map((key) => ({ key, filter: "", count: OPTIONS_PAGE_SIZE })),
  );
  const pagedValueLabelByFacetKey = useMemo(() => {
    const map = new Map<string, Map<string, string>>();
    valueKeys.forEach((key, index) => {
      const codeToDisplay = new Map<string, string>();
      for (const item of valueOptionsQueries[index]?.data?.items ?? []) {
        codeToDisplay.set(item.code, item.display ?? item.code);
      }
      map.set(key, codeToDisplay);
    });
    return map;
  }, [valueKeys, valueOptionsQueries]);
  // A value is only looked up by code once its property's page has settled:
  // before that every value looks unanswered, and the page itself would have
  // answered most of them a moment later.
  const pagedIsSettledByFacetKey = useMemo(() => {
    const map = new Map<string, boolean>();
    valueKeys.forEach((key, index) => {
      map.set(key, !(valueOptionsQueries[index]?.isPending ?? true));
    });
    return map;
  }, [valueKeys, valueOptionsQueries]);

  // A selected value the page did not answer (past its size, or since removed
  // from the property's value set) is resolved by code instead (ADR-0038).
  const unresolvedCodesByFacetKey = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const { facetKey, value } of activeFilters) {
      if (
        definitionByKey.get(facetKey)?.form_control.control !== "concept_picker" ||
        !pagedIsSettledByFacetKey.get(facetKey) ||
        pagedValueLabelByFacetKey.get(facetKey)?.has(value)
      ) {
        continue;
      }
      const codes = map.get(facetKey) ?? [];
      // Past the route's ceiling the request would 422 and lose every label in
      // the facet, so the batch is cut to it instead.
      if (!codes.includes(value) && codes.length < MAX_RESOLVE_CODES) {
        codes.push(value);
      }
      map.set(facetKey, codes);
    }
    return map;
  }, [
    activeFilters,
    definitionByKey,
    pagedIsSettledByFacetKey,
    pagedValueLabelByFacetKey,
  ]);
  const unresolvedFacetKeys = useMemo(
    () => Array.from(unresolvedCodesByFacetKey.keys()),
    [unresolvedCodesByFacetKey],
  );
  const resolveQueries = usePropertyValueResolveQueries(
    unresolvedFacetKeys.map((key) => ({
      key,
      codes: unresolvedCodesByFacetKey.get(key) ?? [],
    })),
  );
  const valueLabelByFacetKey = useMemo(() => {
    const map = new Map<string, Map<string, string>>();
    for (const [key, codeToDisplay] of pagedValueLabelByFacetKey) {
      map.set(key, new Map(codeToDisplay));
    }
    unresolvedFacetKeys.forEach((key, index) => {
      const codeToDisplay = map.get(key) ?? new Map<string, string>();
      for (const item of resolveQueries[index]?.data?.items ?? []) {
        codeToDisplay.set(item.code, item.display ?? item.code);
      }
      map.set(key, codeToDisplay);
    });
    return map;
  }, [pagedValueLabelByFacetKey, unresolvedFacetKeys, resolveQueries]);

  const comboboxes = useMemo(() => {
    const built: {
      key: string;
      label: string;
      options: MultiSelectOption[];
    }[] = [
      {
        key: "status",
        label: "Status",
        options: STATUS_OPTIONS.map(({ value, label }) => ({ value, label })),
      },
    ];
    for (const key of comboboxPropertyKeys) {
      built.push({
        key,
        label: definitionByKey.get(key)?.label ?? key,
        options: Array.from(valueLabelByFacetKey.get(key) ?? [], ([value, label]) => ({
          value,
          label,
        })),
      });
    }
    return built;
  }, [comboboxPropertyKeys, definitionByKey, valueLabelByFacetKey]);

  const optionsFailed = valueOptionsQueries.some((query) => query.isError);

  const { chips, filterByChipKey } = useMemo(() => {
    const byKey = new Map<string, { facetKey: string; value: string }>();
    const built: ActiveFilter[] = activeFilters.map(({ facetKey, value }) => {
      const key = JSON.stringify([facetKey, value]);
      byKey.set(key, { facetKey, value });
      const isCoded =
        definitionByKey.get(facetKey)?.form_control.control === "concept_picker";
      return {
        key,
        facetLabel:
          facetKey === "status"
            ? "Status"
            : (definitionByKey.get(facetKey)?.label ?? facetKey),
        valueLabel:
          facetKey === "status"
            ? statusLabelFor(value)
            : isCoded
              ? (valueLabelByFacetKey.get(facetKey)?.get(value) ?? value)
              : value,
      };
    });
    return { chips: built, filterByChipKey: byKey };
  }, [activeFilters, definitionByKey, valueLabelByFacetKey]);

  const listQuery = useAdminEntriesList({
    limit: PAGE_SIZE,
    after: search.after,
    sort: search.sort,
    filters,
    enabled: mode === "browse",
    keepPreviousPage: true,
  });
  const searchQuery = useAdminSearch({
    q: search.q,
    limit: PAGE_SIZE,
    after: search.after,
    filters,
    enabled: mode === "search",
    keepPreviousPage: true,
  });
  const active = mode === "browse" ? listQuery : searchQuery;

  // Placeholder data is the previous page, kept so a focused paging control
  // stays mounted while the next page loads. It counts as this screen's data
  // only when it answers the same mode and query; otherwise it is another
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

  // The draft mirrors `search.q`, so Back, Forward or a pasted link shows its
  // query in the box, yet stays editable between keystrokes.
  const [queryDraft, setQueryDraft] = useState(search.q);
  const [committedQ, setCommittedQ] = useState(search.q);
  if (search.q !== committedQ) {
    setCommittedQ(search.q);
    setQueryDraft(search.q);
  }

  const paging = useKeysetPaging(search.after);

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

  // Held until the re-sorted page arrives and spoken with its result count: an
  // announcement made now would be replaced by that one within moments.
  const sortNoteRef = useRef<string | null>(null);

  // Only reachable in browse mode: the control is disabled while searching.
  function handleSortChange(event: ChangeEvent<HTMLSelectElement>) {
    const sort = event.target.value as AdminListingSort;
    sortNoteRef.current = `Sorted by ${sortLabel(sort)}.`;
    void navigate({ search: (prev) => changeSort(prev, sort) });
  }

  function handleClearAllFilters() {
    void navigate({ search: (prev) => clearAllFilters(prev) });
  }

  function handleNextPage() {
    // A placeholder's `next_cursor` points at the page already loading, so
    // acting on it would push a duplicate onto the Previous stack.
    if (nextCursor !== null && !active.isPlaceholderData) {
      paging.next(nextCursor);
      void navigate({ search: (prev) => ({ ...prev, after: nextCursor }) });
    }
  }

  function handlePreviousPage() {
    const previousAfter = paging.previous();
    void navigate({ search: (prev) => ({ ...prev, after: previousAfter }) });
  }

  const staleData = active.isError && data !== undefined;
  const hardFailure = active.isError && data === undefined;
  const hardFailureMessage = refusalDetail(active.error) ?? LOAD_FAILURE;

  // `data` keeps its identity across a refetch that returns the same page, so
  // this speaks once per new result set. A placeholder is never announced.
  const resultMessage =
    data && !active.isError && !active.isPlaceholderData
      ? resultAnnouncement(data.items.length, data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(
        sortNoteRef.current === null
          ? resultMessage
          : `${sortNoteRef.current} ${resultMessage}`,
      );
      sortNoteRef.current = null;
    }
  }, [data, resultMessage, announce]);

  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  useEffect(() => {
    if (hardFailure) {
      sortNoteRef.current = null;
      announce(hardFailureMessage);
    }
  }, [hardFailure, hardFailureMessage, announce]);

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
    <section aria-labelledby="catalogue-list-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="flex flex-col gap-4 py-6">
        <PageHeader id="catalogue-list-heading" title="Catalogue administration" />

        <div className="flex flex-wrap items-end gap-4">
          <SearchInput
            id="catalogue-list-query"
            label="Search term or SNOMED CT code"
            value={queryDraft}
            onValueChange={setQueryDraft}
            onSubmit={handleSearchSubmit}
            className="min-w-64 flex-1"
          />

          <Select
            id="catalogue-list-sort"
            label="Sort by"
            value={
              mode === "search" ? SEARCH_MODE_SORT_VALUE : (search.sort ?? "business_key")
            }
            onChange={handleSortChange}
            disabled={mode === "search"}
            options={[
              ...(mode === "search"
                ? [{ value: SEARCH_MODE_SORT_VALUE, label: "Relevance" }]
                : []),
              ...SORT_OPTIONS,
            ]}
            className="min-h-10"
          />
        </div>

        <div className="flex flex-wrap items-start gap-4">
          {comboboxes.map((combobox) => (
            <MultiSelectCombobox
              key={combobox.key}
              id={`catalogue-list-facet-${combobox.key}`}
              label={combobox.label}
              options={combobox.options}
              selected={filters[combobox.key] ?? []}
              onToggle={(value) => handleFilterToggle(combobox.key, value)}
              maxSelected={MAX_VALUES_PER_FILTER}
            />
          ))}
        </div>

        {optionsFailed ? (
          <p className="m-0 text-sm text-[var(--color-text-muted)]">
            {OPTIONS_UNAVAILABLE}
          </p>
        ) : null}

        {/* Outside the `data` gate: removing a filter is the way out of a
            refused request, so it stays reachable on failure. */}
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
                          to="/admin/catalogue/$businessKey/edit"
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
                    key: "business_key",
                    header: "Identifier",
                    render: (row: Row) => (
                      <span className="font-mono">{row.business_key}</span>
                    ),
                  },
                  {
                    key: "status",
                    header: "Status",
                    render: (row: Row) => (
                      <StatusBadge
                        tone={statusToneFor(row.status)}
                        label={statusLabelFor(row.status)}
                      />
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
                    key: "updated_at",
                    header: "Last changed",
                    align: "right",
                    render: (row: Row) => (
                      <span className="tabular-nums">
                        {new Date(row.updated_at).toLocaleString()}
                      </span>
                    ),
                  },
                ]}
                rows={items}
                getRowKey={(row) => row.business_key}
                emptyState={emptyState}
              />
            </div>

            {(items.length > 0 || paging.hasPrevious) && (
              <Pagination
                hasNext={nextCursor !== null}
                onNext={handleNextPage}
                onPrevious={paging.hasPrevious ? handlePreviousPage : undefined}
                className="self-start"
              />
            )}
          </>
        )}
      </PageContainer>
    </section>
  );
}
