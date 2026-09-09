import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import {
  useAdminEntriesList,
  useAdminSearch,
  usePropertyDefinitions,
  usePropertyValueOptionsQueries,
  usePropertyValueResolveQueries,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { AdminCatalogueFilterPanel } from "../catalogue/admin-catalogue-filter-panel.tsx";
import { BulkOutcomeSummary, tallyText } from "../catalogue/bulk-outcome-summary.tsx";
import { BulkReclassifyDialog } from "../catalogue/bulk-reclassify-dialog.tsx";
import { BulkReclassifyToolbar } from "../catalogue/bulk-reclassify-toolbar.tsx";
import { STATUS_OPTIONS } from "../catalogue/status-options.ts";
import { DataTable } from "../components/data-table.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import {
  activeFilterEntries,
  clearAllFilters,
  filterSelections,
  toggleFilterValue,
} from "../router/search-params.ts";

/**
 * The admin catalogue list screen (issue #267; FR-14, FR-15, FR-16, FR-36,
 * NFR-31) - the landing page for `/admin/catalogue/`, replacing the
 * placeholder that named no owning issue. Finds a draft, active, deprecated
 * or withdrawn entry and links to its existing edit screen (#61); selects
 * rows, carrying each one's `row_version`, for #63's bulk reclassify to act
 * on later.
 *
 * **Dual-surface dispatch** (ADR-0032): an empty `q` browses
 * `GET /catalogue/admin/entries` (no facet counts); a non-empty `q` searches
 * `GET /catalogue/admin/search` (facets with counts) instead. Both routes
 * accept the identical `filter.*` parameters, so the filter panel does not
 * branch on which surface is active - only the two query hooks below do.
 *
 * **No page number, a forward-only cursor** (ADR-0024): both admin
 * collection routes are keyset-paginated, so there is no "page 3" to
 * restore, only "the cursor from the last page seen". "Next page" pushes a
 * new `after` into the URL; there is no "previous page" control - the
 * browser Back button already restores the prior `after` from history.
 *
 * **Selection persists across a page change but not across a new
 * population** (issue #267 plan, open question 1): paging forward keeps
 * whatever was already checked, but a new `q` or filter set clears it - the
 * population an administrator was choosing from no longer exists, and
 * carrying a stale selection across that boundary would silently point
 * #63's bulk route at rows nobody actually saw checked.
 */

// TanStack Router keys `useSearch`/`useParams`' own `from` off the route's
// internal id, which includes a pathless layout segment like `authenticated`
// - matching `admin-catalogue-edit.tsx`'s own `from`. `useNavigate`/`Link`'s
// `from`/`to`, by contrast, are keyed off the resolved URL path, which never
// includes a pathless segment - hence the two different constants below,
// confirmed against the router's own registered route ids rather than
// assumed from the URL alone.
const ROUTE_ID = "/authenticated/admin/catalogue/" as const;
const ROUTE_PATH = "/admin/catalogue/" as const;

type Row =
  components["schemas"]["AdminEntrySummary"] | components["schemas"]["AdminSearchHit"];

function emptyStateText(mode: "browse" | "search", q: string): string {
  return mode === "search"
    ? `No catalogue entries match "${q}".`
    : "No catalogue entries match this filter.";
}

function selectionAnnouncement(count: number): string {
  if (count === 0) {
    return "No rows selected.";
  }
  return `${count} row${count === 1 ? "" : "s"} selected.`;
}

type PropertyDefinition = components["schemas"]["PropertyDefinitionResponse"];

/**
 * A facet's display name for the active-filter chip row (issue #289). `status`
 * is special-cased the same way `AdminCatalogueFilterPanel` special-cases it
 * (a core column, not a registry property); every other key resolves against
 * the registry's own label when one exists, keyed on the **unfiltered**
 * definitions map so a deprecated or non-`concept_picker` property still
 * resolves - only a key genuinely absent from the registry (the true escape
 * hatch, PR #285 review finding 1) falls back to the raw key.
 */
function resolveFacetLabel(
  facetKey: string,
  definitionByKey: Map<string, PropertyDefinition>,
): string {
  if (facetKey === "status") {
    return "Status";
  }
  return definitionByKey.get(facetKey)?.label ?? facetKey;
}

/**
 * A facet value's display string for the active-filter chip row (issue #289,
 * #306). `status` resolves against `STATUS_OPTIONS`; a `concept_picker`
 * property resolves against `valueLabelByFacetKey`, which merges its
 * unfiltered value-options page with the resolve-by-code lookup for whatever
 * that page did not answer (issue #306, ADR-0038) - so a selected value
 * beyond `DEFAULT_PAGE_SIZE`, or one the RCPA has since removed from the
 * value set, still resolves. Only a code neither side can resolve at all
 * (never existed, or the fetch is still pending/erroring) falls back to the
 * raw code, mirroring `PropertyFacetGroup`'s own `carriedOptions` fallback.
 * Every other case (an unrecognised key, or a registry property with no
 * value-options source, e.g. `volume_ml`) has nothing to resolve the value
 * against, so it stays raw.
 */
function resolveValueLabel(
  facetKey: string,
  value: string,
  definitionByKey: Map<string, PropertyDefinition>,
  valueLabelByFacetKey: Map<string, Map<string, string>>,
): string {
  if (facetKey === "status") {
    return STATUS_OPTIONS.find((option) => option.value === value)?.label ?? value;
  }
  if (definitionByKey.get(facetKey)?.form_control.control === "concept_picker") {
    return valueLabelByFacetKey.get(facetKey)?.get(value) ?? value;
  }
  return value;
}

/** Matching `admin-catalogue-edit.tsx`'s own `staleWarning` - one string,
 * both shown and announced, so the two cannot drift apart. */
const STALE_DATA_WARNING =
  "Catalogue entries could not be refreshed just now, so what follows may be out of date.";

export function AdminCatalogueListPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_PATH });
  const { message, politeness, announce } = useAnnounce();

  const filters = useMemo(() => filterSelections(search), [search]);
  const mode: "browse" | "search" = search.q.trim().length > 0 ? "search" : "browse";

  // Every active `filter.*` selection, flattened - deliberately not derived
  // from `filters` (the panel's own recognised-facet shape): a facet the
  // panel does not render a control for (not `concept_picker`, or dropped
  // from the registry since the link was shared) still needs a way to be
  // seen and cleared (PR #285 review finding 1).
  const activeFilters = useMemo(() => activeFilterEntries(search), [search]);

  // A second `usePropertyDefinitions()` call (issue #289) - same query key as
  // `AdminCatalogueFilterPanel`'s own, so this is a cache read, not a second
  // network request. Keyed by the **unfiltered** `.data.items`, unlike the
  // panel's own `codedFilterableProperties`: a chip must still resolve a
  // label for a deprecated or non-`concept_picker` property, since neither of
  // those reasons for the panel omitting a control makes the property's own
  // name unknown.
  const definitions = usePropertyDefinitions();
  const definitionByKey = useMemo(() => {
    const map = new Map<string, PropertyDefinition>();
    for (const definition of definitions.data?.items ?? []) {
      map.set(definition.key, definition);
    }
    return map;
  }, [definitions.data]);

  // The active facets worth a value-options fetch - a `concept_picker`
  // property's stored value is a code, uninterpretable without the page
  // `PropertyFacetGroup` itself fetches (issue #289). Deduplicated by facet
  // key: several selected values on the same coded facet share one fetch.
  const codedActiveFacetKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const { facetKey } of activeFilters) {
      if (definitionByKey.get(facetKey)?.form_control.control === "concept_picker") {
        keys.add(facetKey);
      }
    }
    return Array.from(keys);
  }, [activeFilters, definitionByKey]);
  const valueOptionsQueries = usePropertyValueOptionsQueries(
    codedActiveFacetKeys.map((key) => ({ key, filter: "" })),
  );
  const pagedValueLabelByFacetKey = useMemo(() => {
    const map = new Map<string, Map<string, string>>();
    codedActiveFacetKeys.forEach((key, index) => {
      const codeToDisplay = new Map<string, string>();
      for (const item of valueOptionsQueries[index]?.data?.items ?? []) {
        codeToDisplay.set(item.code, item.display ?? item.code);
      }
      map.set(key, codeToDisplay);
    });
    return map;
  }, [codedActiveFacetKeys, valueOptionsQueries]);
  // Whether each facet's own unfiltered page (above) has settled - success
  // or error, either way. Read below to hold off resolving by code until a
  // facet's own page has actually had its chance to answer first: without
  // this, every active value looks "not yet answered" on the render before
  // the page query returns, firing a resolve request the page itself would
  // have answered a moment later (a real, avoidable extra fetch, not just an
  // extra cache read).
  const pagedIsSettledByFacetKey = useMemo(() => {
    const map = new Map<string, boolean>();
    codedActiveFacetKeys.forEach((key, index) => {
      map.set(key, !(valueOptionsQueries[index]?.isPending ?? true));
    });
    return map;
  }, [codedActiveFacetKeys, valueOptionsQueries]);

  // A selected value the unfiltered page above did not answer - beyond its
  // `DEFAULT_PAGE_SIZE`, or since removed from the property's bound value
  // set - resolved directly by code instead (issue #306, ADR-0038), rather
  // than left to fall back to the raw code the way #289 originally left it.
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
      // Capped at 200, matching the route's own `code` ceiling (review
      // round 1, PR #307): past that, the request itself would 422 and
      // every chip for this facet would fall back to its raw code,
      // including the ones already within the ceiling - a graceful
      // truncation here is strictly better than that all-or-nothing loss.
      if (!codes.includes(value) && codes.length < 200) {
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

  const listQuery = useAdminEntriesList({
    limit: 50,
    after: search.after,
    filters,
    enabled: mode === "browse",
  });
  const searchQuery = useAdminSearch({
    q: search.q,
    limit: 50,
    after: search.after,
    filters,
    enabled: mode === "search",
  });
  const active = mode === "browse" ? listQuery : searchQuery;
  const items: Row[] = active.data?.items ?? [];
  const nextCursor = active.data?.next_cursor ?? null;

  // business_key -> row_version, so a selected row carries FR-38's token
  // without #63's bulk route re-reading it (issue #267's own acceptance
  // criterion). A page's rows are the only place this value can come from -
  // it is set at the moment a row is checked, from whatever `items` holds
  // then, and is not refreshed just because the underlying page reloads.
  const [selected, setSelected] = useState<Map<string, number>>(new Map());

  // Reset during render, not in an effect (React's own "adjusting state when
  // a prop changes" pattern) - `populationKey` changing *is* the signal that
  // the previous selection no longer names a population that still exists,
  // so there is nothing to synchronise with an external system here, only
  // React state to keep consistent with itself before this render commits.
  const populationKey = useMemo(
    () => JSON.stringify({ q: search.q, filters }),
    [search.q, filters],
  );
  const [committedPopulationKey, setCommittedPopulationKey] = useState(populationKey);
  if (populationKey !== committedPopulationKey) {
    setCommittedPopulationKey(populationKey);
    setSelected(new Map());
  }

  const hasAnnouncedRef = useRef(false);
  // Set from the bulk-reclassify completion handler right before it clears
  // the selection (below), so that clearing's own "No rows selected." does
  // not overwrite the more informative reclassify-outcome announcement
  // racing it - both go through `useAnnounce`'s identical `setTimeout(0)`,
  // and the selection effect below runs after the completion handler's own
  // render, so without this guard its announcement is the one left
  // standing. Reset by the `bulkResult` effect further down, not by this
  // one: tying the reset to `bulkResult` (set in the exact same handler that
  // sets this flag) rather than to `selected.size` (which the flag's own
  // setter also happens to change) keeps the two independent, so a future
  // change to either effect's trigger can't strand the flag set (PR #290
  // review).
  const suppressSelectionAnnouncementRef = useRef(false);
  useEffect(() => {
    if (!hasAnnouncedRef.current) {
      hasAnnouncedRef.current = true;
      return;
    }
    if (suppressSelectionAnnouncementRef.current) {
      return;
    }
    announce(selectionAnnouncement(selected.size));
  }, [selected.size, announce]);

  // Issue #63's bulk reclassify. `bulkResult` is a durable record shown on
  // this screen after the dialog closes, not tied to the dialog's own
  // lifetime - an operator who scrolls away and back still sees what the
  // last batch did, until the next one replaces it.
  const [bulkDialogOpen, setBulkDialogOpen] = useState(false);
  const [bulkResult, setBulkResult] = useState<{
    result: components["schemas"]["BulkSavePropertyValuesResult"];
    propertyLabel: string;
  } | null>(null);
  const bulkResultsSectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    suppressSelectionAnnouncementRef.current = false;
    // Moves focus to the results section once it exists, since `Dialog`'s
    // own focus-restore (`dialog.tsx`) targets whatever triggered it - the
    // "Reclassify selected" toolbar button - and that button unmounts the
    // moment the selection it completed against is cleared, dropping focus
    // to `<body>` for a keyboard or screen-reader operator right as this
    // section appears (PR #290 review).
    if (bulkResult) {
      bulkResultsSectionRef.current?.focus();
    }
  }, [bulkResult]);

  // Same render-time-adjustment pattern as the selection reset above: the
  // draft mirrors `search.q` (so Back/Forward or a pasted link's `q` shows
  // in the box) but must still be freely editable between keystrokes and
  // the eventual submit, which is exactly what a plain `useState` plus this
  // one-render correction gives without an effect re-render on every value.
  const [queryDraft, setQueryDraft] = useState(search.q);
  const [committedQ, setCommittedQ] = useState(search.q);
  if (search.q !== committedQ) {
    setCommittedQ(search.q);
    setQueryDraft(search.q);
  }

  function handleSearchSubmit(event: FormEvent) {
    event.preventDefault();
    // Trimmed before it ever reaches the URL (PR #285 review finding 5): a
    // whitespace-only value used to land in `q` untrimmed while `mode` (and,
    // below, `useAdminSearch`'s own guard) is computed with `.trim()` -
    // agreeing that this is browse, while a stray `q=%20` sat in the address
    // bar claiming otherwise.
    const trimmed = queryDraft.trim();
    void navigate({ search: (prev) => ({ ...prev, q: trimmed, after: undefined }) });
  }

  // Also the chip list's own "remove" handler below - toggling off an
  // already-selected value is exactly "remove it" (`toggleFilterValue`
  // add/removes by whether `value` is already present).
  function handleFilterToggle(facetKey: string, value: string) {
    void navigate({ search: (prev) => toggleFilterValue(prev, facetKey, value) });
  }

  function handleClearAllFilters() {
    void navigate({ search: (prev) => clearAllFilters(prev) });
  }

  function handleNextPage() {
    if (nextCursor !== null) {
      void navigate({ search: (prev) => ({ ...prev, after: nextCursor }) });
    }
  }

  const staleData = active.isError && active.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  // A hard failure (no prior data to fall back on) was rendered but never
  // announced (PR #285 review finding 3) - a screen-reader user who submits
  // a search or filter selection that 4xxs (a realistic path once finding 1's
  // unrecognised `filter.*` reaches the server) got silence. The message text
  // is derived the same way it is rendered below, so the two cannot drift.
  const hardFailure = active.isError && active.data === undefined;
  const hardFailureMessage =
    refusalDetail(active.error) ??
    "Catalogue entries could not be loaded. Try again, or contact an administrator if the problem persists.";
  useEffect(() => {
    if (hardFailure) {
      announce(hardFailureMessage);
    }
  }, [hardFailure, hardFailureMessage, announce]);

  return (
    <section aria-labelledby="catalogue-list-heading">
      <LiveRegion message={message} politeness={politeness} />

      <h1 id="catalogue-list-heading">Catalogue administration</h1>

      <form role="search" onSubmit={handleSearchSubmit}>
        <label htmlFor="catalogue-list-query">Search term or SNOMED CT code</label>
        <input
          id="catalogue-list-query"
          type="text"
          value={queryDraft}
          onChange={(event) => setQueryDraft(event.target.value)}
        />
        <button type="submit">Search</button>
      </form>

      <AdminCatalogueFilterPanel selections={filters} onToggle={handleFilterToggle} />

      {/* Kept outside the `active.data &&` gate below, deliberately: this is
          the one control that must stay reachable even while the listing
          itself is refused (e.g. a filter the server no longer recognises),
          since it is the only way out of that state (PR #285 review
          finding 1). */}
      {activeFilters.length > 0 && (
        <div
          role="group"
          aria-label="Active filters"
          className="flex flex-wrap items-center gap-2"
        >
          {activeFilters.map(({ facetKey, value }) => {
            const facetLabel = resolveFacetLabel(facetKey, definitionByKey);
            const valueLabel = resolveValueLabel(
              facetKey,
              value,
              definitionByKey,
              valueLabelByFacetKey,
            );
            return (
              <button
                key={`${facetKey}:${value}`}
                type="button"
                aria-label={`Remove filter ${facetLabel}: ${valueLabel}`}
                onClick={() => handleFilterToggle(facetKey, value)}
              >
                {facetLabel}: {valueLabel}
                <span aria-hidden="true"> ✕</span>
              </button>
            );
          })}
          <button type="button" onClick={handleClearAllFilters}>
            Clear all filters
          </button>
        </div>
      )}

      {active.isPending && <p>Loading catalogue entries…</p>}

      {hardFailure && <p>{hardFailureMessage}</p>}

      {staleData && <p>{STALE_DATA_WARNING}</p>}

      {active.data && (
        <>
          <BulkReclassifyToolbar
            selectedCount={selected.size}
            onLaunch={() => {
              // Cleared here, not left to `onComplete`'s next call: a batch
              // that aborts whole (FR-89's 422) leaves the dialog open with
              // nothing applied, and without this the *previous* batch's
              // tallies would still be showing behind it, reading as this
              // batch's own outcome (PR #290 review).
              setBulkResult(null);
              setBulkDialogOpen(true);
            }}
          />

          {bulkResult && (
            <BulkOutcomeSummary
              ref={bulkResultsSectionRef}
              result={bulkResult.result}
              propertyLabel={bulkResult.propertyLabel}
            />
          )}

          <DataTable
            caption="Catalogue entries"
            columns={[
              {
                key: "business_key",
                header: "Code",
                isRowHeader: true,
                render: (row: Row) => (
                  <Link
                    to="/admin/catalogue/$businessKey/edit"
                    params={{ businessKey: row.business_key }}
                  >
                    {row.business_key}
                  </Link>
                ),
              },
              {
                key: "preferred_term",
                header: "Requesting term",
                render: (row: Row) => row.preferred_term,
              },
              { key: "status", header: "Status", render: (row: Row) => row.status },
              {
                key: "updated_at",
                header: "Last changed",
                render: (row: Row) => new Date(row.updated_at).toLocaleString(),
              },
            ]}
            rows={items}
            getRowKey={(row) => row.business_key}
            emptyState={emptyStateText(mode, search.q)}
            selection={{
              selectedKeys: new Set(selected.keys()),
              selectAllLabel: "Select all rows on this page",
              getRowLabel: (row) => `Select ${row.business_key}`,
              onSelectRow: (key, isSelected) => {
                setSelected((current) => {
                  const next = new Map(current);
                  if (isSelected) {
                    const row = items.find((item) => item.business_key === key);
                    if (row) {
                      next.set(key, row.row_version);
                    }
                  } else {
                    next.delete(key);
                  }
                  return next;
                });
              },
              onSelectAll: (isSelected) => {
                setSelected((current) => {
                  const next = new Map(current);
                  for (const row of items) {
                    if (isSelected) {
                      next.set(row.business_key, row.row_version);
                    } else {
                      next.delete(row.business_key);
                    }
                  }
                  return next;
                });
              },
            }}
          />

          {nextCursor !== null && (
            <button type="button" onClick={handleNextPage}>
              Next page
            </button>
          )}
        </>
      )}

      {bulkDialogOpen && (
        <BulkReclassifyDialog
          entries={Array.from(selected, ([business_key, expected_row_version]) => ({
            business_key,
            expected_row_version,
          }))}
          onClose={() => setBulkDialogOpen(false)}
          onComplete={(result, propertyLabel) => {
            setBulkDialogOpen(false);
            // Every captured `expected_row_version` is stale the moment
            // anything applied - refreshing them from the outcome list
            // instead would let a second submit blind-overwrite whatever a
            // concurrent editor did in between (issue #63 plan). The results
            // panel below is the durable record of what to revisit.
            suppressSelectionAnnouncementRef.current = true;
            setSelected(new Map());
            setBulkResult({ result, propertyLabel });
            announce(`Reclassify ${propertyLabel}: ${tallyText(result)}`);
          }}
        />
      )}
    </section>
  );
}
