import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useAdminEntriesList, useAdminSearch } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { AdminCatalogueFilterPanel } from "../catalogue/admin-catalogue-filter-panel.tsx";
import { DataTable } from "../components/data-table.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { filterSelections, toggleFilterValue } from "../router/search-params.ts";

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
  useEffect(() => {
    if (!hasAnnouncedRef.current) {
      hasAnnouncedRef.current = true;
      return;
    }
    announce(selectionAnnouncement(selected.size));
  }, [selected.size, announce]);

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
    void navigate({ search: (prev) => ({ ...prev, q: queryDraft, after: undefined }) });
  }

  function handleFilterToggle(facetKey: string, value: string) {
    void navigate({ search: (prev) => toggleFilterValue(prev, facetKey, value) });
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

      {active.isPending && <p>Loading catalogue entries…</p>}

      {active.isError && active.data === undefined && (
        <p>
          {refusalDetail(active.error) ??
            "Catalogue entries could not be loaded. Try again, or contact an administrator if the problem persists."}
        </p>
      )}

      {staleData && <p>{STALE_DATA_WARNING}</p>}

      {active.data && (
        <>
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
    </section>
  );
}
