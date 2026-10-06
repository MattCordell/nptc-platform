import type { SearchSchemaInput } from "@tanstack/react-router";

/**
 * Hand-written search-param validators for the route table.
 *
 * No schema library (zod/valibot) is used here deliberately. The rule these
 * validators enforce is "never coerce a code" - a code is a string end to end
 * (FR-06) - and that is a schema library's least ergonomic mode: the
 * convenient path in most of them (`z.coerce.number()`) is the exact hazard
 * this file exists to avoid. A function that visibly calls nothing but
 * `String()` on a code is easier to review against FR-06 than a schema where
 * the reviewer must confirm nobody reached for a coercing helper. See
 * ADR-0020.
 *
 * Every validator degrades to a safe default instead of throwing: a mistyped
 * `sort=` should show the default order, not an error screen.
 */

/**
 * A malformed value degrades to `fallback` rather than throwing. Also the
 * guard if the router's search parser is ever changed back to its default
 * (which runs `JSON.parse` over every value): a numeric-looking `code` would
 * arrive here as a `number`, and this still returns a string, so the failure
 * stays visible (an empty value) instead of silently losing precision on an
 * 18-digit SCTID.
 */
function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/**
 * FR-17: `/catalogue/lookup?system={uri}&code={code}`, for callers holding
 * the full system URI rather than a registered `system_token` alias.
 */
export interface LookupSearch {
  system: string;
  /**
   * Always a string (FR-06). Leading zeros are significant and an 18-digit
   * SCTID exceeds `Number.MAX_SAFE_INTEGER` - never `Number()` this.
   */
  code: string;
}

export type LookupSearchInput = Partial<LookupSearch> & SearchSchemaInput;

export function validateLookupSearch(search: Record<string, unknown>): LookupSearch {
  return { system: asString(search.system), code: asString(search.code) };
}

/** `/releases/compare?from={releaseId}&to={releaseId}` (FR-60). */
export interface ReleaseCompareSearch {
  from: string;
  to: string;
}

export type ReleaseCompareSearchInput = Partial<ReleaseCompareSearch> & SearchSchemaInput;

export function validateReleaseCompareSearch(
  search: Record<string, unknown>,
): ReleaseCompareSearch {
  return { from: asString(search.from), to: asString(search.to) };
}

/** `/sign-in?redirect={path}` - #41 reads this to return the user where they were. */
export interface SignInSearch {
  redirect?: string;
}

export type SignInSearchInput = Partial<SignInSearch> & SearchSchemaInput;

/**
 * `redirect` must be an internal, same-origin path - never a value #41's
 * post-login redirect could send a signed-in user off-site to (an open
 * redirect). Rejects anything that isn't a single leading `/`:
 * `https://evil.example/`, protocol-relative `//evil.example` (a bare `/`
 * followed by another `/` is host-relative, not path-relative), and
 * `javascript:...`/backslash variants some browsers still normalise into a
 * host-relative URL all fail the check and are dropped, same as an absent
 * `redirect`.
 */
export function asInternalRedirect(value: unknown): string | undefined {
  const candidate = asString(value);
  if (
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\")
  ) {
    return undefined;
  }
  return candidate;
}

export function validateSignInSearch(search: Record<string, unknown>): SignInSearch {
  const redirect = asInternalRedirect(search.redirect);
  return redirect ? { redirect } : {};
}

// --- catalogue lists: public and admin -----------------------------------

/**
 * The `filter.<key>` parameter name prefix (ADR-0032, matching the backend's
 * own `FILTER_PARAM_PREFIX` in `nptc.catalogue.facets`). Declared once here
 * rather than repeated as a string literal at every call site in this module
 * and in `api/filter-params.ts`.
 */
export const FILTER_PARAM_PREFIX = "filter.";

/**
 * A `filter.*` value, normalised to an array of strings.
 *
 * `parseSearch` (router.tsx) gives a *bare string* for a parameter that
 * appears exactly once in the URL and an array only once it is repeated -
 * `?filter.status=draft` and `?filter.status=draft&filter.status=active`
 * arrive as different shapes for the identical concept (one selected value
 * vs. two). This always returns an array, so every consumer of a validated
 * search - the filter panel, `filterSelections`, `filterQueryParams` - sees
 * one shape regardless of how many values were selected. Never `Number()`s a
 * value: a facet value can be a SNOMED CT code (FR-06).
 */
function asStringArray(value: unknown): string[] {
  const items = Array.isArray(value) ? value : [value];
  return items.map((item) => asString(item)).filter((item) => item.length > 0);
}

/**
 * The `sort` values `GET /catalogue/admin/entries` accepts (issue #287),
 * matching `nptc.catalogue.maintenance.SortName` on the backend. Only the
 * *browse* surface is sortable - `GET /catalogue/admin/search` stays
 * relevance-ranked, and the public endpoints offer no sort at all.
 */
const ADMIN_LISTING_SORTS = [
  "business_key",
  "preferred_term",
  "updated_at",
  "status",
] as const;
export type AdminListingSort = (typeof ADMIN_LISTING_SORTS)[number];

function asAdminListingSort(value: unknown): AdminListingSort {
  const candidate = asString(value);
  return (ADMIN_LISTING_SORTS as readonly string[]).includes(candidate)
    ? (candidate as AdminListingSort)
    : "business_key";
}

/**
 * The URL state both catalogue list screens share: `q`, a keyset cursor and
 * the `filter.<key>` selections (ADR-0024, ADR-0032).
 *
 * Deliberately flat, not `{ q, after, filters: Record<string, string[]> }`:
 * `router.tsx`'s own `stringifySearch` throws for a non-scalar value, so a
 * nested record would break the URL round trip. Each `filter.<key>` stays its
 * own top-level key, exactly as it appears on the wire - `filterSelections`
 * below turns it back into a keyed record.
 *
 * `after` is a cursor, not a page number: both list endpoints are
 * keyset-paginated, so there is no page number to restore, only the cursor
 * from the last page the caller saw.
 */
export type FilteredListSearch = {
  q: string;
  after?: string;
} & {
  [key: `${typeof FILTER_PARAM_PREFIX}${string}`]: string[] | undefined;
};

/**
 * Search state for the public `/catalogue` screen (FR-14..16).
 * Encoded entirely in the URL, so a pasted link reproduces the same results.
 * It has no `sort`: the public endpoints offer none.
 */
export type CatalogueSearch = FilteredListSearch;

/**
 * What a caller may supply when navigating *to* `/catalogue` - every field
 * optional, so `<Link to="/catalogue">` needs no search prop at all. The
 * `SearchSchemaInput` brand is TanStack Router's mechanism for giving a
 * route a narrower input type than its validated output type; `route-tree.ts`
 * applies it with a type-only cast on the validator when registering the
 * route, so the validator itself keeps a plain, easily unit-tested
 * `Record<string, unknown>` parameter.
 */
export type CatalogueSearchInput = Partial<CatalogueSearch> & SearchSchemaInput;

function validateFilteredListSearch(search: Record<string, unknown>): FilteredListSearch {
  const validated: FilteredListSearch = { q: asString(search.q) };
  const after = asString(search.after);
  if (after.length > 0) {
    validated.after = after;
  }
  for (const [key, value] of Object.entries(search)) {
    if (!key.startsWith(FILTER_PARAM_PREFIX)) {
      continue;
    }
    const values = asStringArray(value);
    if (values.length > 0) {
      validated[key as `${typeof FILTER_PARAM_PREFIX}${string}`] = values;
    }
  }
  return validated;
}

export function validateCatalogueSearch(
  search: Record<string, unknown>,
): CatalogueSearch {
  return validateFilteredListSearch(search);
}

/**
 * Search state for `/admin/catalogue/` (issue #267, FR-16, FR-36): the shared
 * list state plus an optional `sort` (issue #287). `sort` is optional, like
 * `after`, because `business_key` is the default both here and on the
 * backend, so there is nothing to gain from always writing it into the URL.
 */
export type AdminCatalogueSearch = FilteredListSearch & {
  sort?: AdminListingSort;
};

/**
 * What a caller may supply when navigating *to* `/admin/catalogue/` -
 * matching `CatalogueSearchInput`'s own reasoning.
 */
export type AdminCatalogueSearchInput = Partial<AdminCatalogueSearch> & SearchSchemaInput;

export function validateAdminCatalogueSearch(
  search: Record<string, unknown>,
): AdminCatalogueSearch {
  const validated: AdminCatalogueSearch = validateFilteredListSearch(search);
  const sort = asAdminListingSort(search.sort);
  if (sort !== "business_key") {
    validated.sort = sort;
  }
  return validated;
}

// --- property registry list ------------------------------------------------

/**
 * Search state for `/admin/properties/`: `deprecated=show` reveals deprecated
 * properties, and its absence hides them. A string flag rather than a boolean,
 * so the URL round trip through `stringifySearch` and `parseSearch` stays
 * lossless.
 */
export type PropertyListSearch = {
  deprecated?: "show";
};

export type PropertyListSearchInput = Partial<PropertyListSearch> & SearchSchemaInput;

export function validatePropertyListSearch(
  search: Record<string, unknown>,
): PropertyListSearch {
  return asString(search.deprecated) === "show" ? { deprecated: "show" } : {};
}

/**
 * The `filter.*` entries of a validated catalogue list search, keyed by
 * facet alone (the `filter.` prefix stripped) - the shape the filter panel
 * and `api/filter-params.ts`'s `filterQueryParams` both want, so neither
 * re-derives it from the flat validated search object by hand.
 */
export function filterSelections(search: FilteredListSearch): Record<string, string[]> {
  const selections: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(search)) {
    if (key.startsWith(FILTER_PARAM_PREFIX) && Array.isArray(value)) {
      selections[key.slice(FILTER_PARAM_PREFIX.length)] = value;
    }
  }
  return selections;
}

/**
 * Every `filter.*` selection in a validated search, flattened to one entry
 * per selected value (PR #285 review finding 1) - the shape a "what's
 * applied right now" chip list wants. Built from `filterSelections` itself
 * (PR #285 review round 2's own nit) rather than re-scanning `search` a
 * second time: the two would otherwise be two independent implementations of
 * the identical `filter.` prefix strip, free to drift silently apart.
 * Because it reads `filterSelections`'s own output rather than the panel's
 * definition-derived facet list, it still survives a `filter.<key>` the
 * panel does not recognise: a filterable property with no `concept_picker`
 * control, or one dropped from the registry after the link that named it
 * was shared, both still round-trip through here even though neither ever
 * gets a checkbox. Without this, a facet in that state is invisible to the
 * panel and to the URL alike - applied, but with no control on screen that
 * could ever clear it.
 */
export function activeFilterEntries(
  search: FilteredListSearch,
): { facetKey: string; value: string }[] {
  return Object.entries(filterSelections(search)).flatMap(([facetKey, values]) =>
    values.map((value) => ({ facetKey, value })),
  );
}

/**
 * Drops every `filter.*` selection and the `after` cursor, keeping every other
 * key (`q`, and the admin screen's `sort`) - the "Clear all filters" control's
 * own handler, for the same reason `toggleFilterValue` drops `after`: the
 * population being paged over no longer exists once the filter set changes
 * underneath it.
 *
 * `sort` (issue #287) is kept: changing the filter set does not invalidate an
 * ordering the way changing `sort` itself does.
 */
export function clearAllFilters<S extends FilteredListSearch>(search: S): S {
  const cleared: FilteredListSearch = { ...search };
  delete cleared.after;
  for (const key of Object.keys(cleared)) {
    if (key.startsWith(FILTER_PARAM_PREFIX)) {
      delete cleared[key as `${typeof FILTER_PARAM_PREFIX}${string}`];
    }
  }
  return cleared as S;
}

/**
 * Changes `sort` (issue #287) - the browse list screen's own `<select>`
 * `onChange`, matching `toggleFilterValue`'s own shape below.
 *
 * Drops `after` for the identical reason `toggleFilterValue` does: the
 * keyset ordering a cursor was paging over no longer exists once `sort`
 * changes underneath it - `nptc.catalogue.maintenance`'s cursor digest
 * refuses a replayed cursor server-side for exactly this reason (issue
 * #287), and this is that same fact applied before a stale cursor is ever
 * sent.
 */
export function changeSort(
  search: AdminCatalogueSearch,
  sort: AdminListingSort,
): AdminCatalogueSearch {
  const updated: AdminCatalogueSearch = { ...search };
  delete updated.after;
  if (sort === "business_key") {
    delete updated.sort;
  } else {
    updated.sort = sort;
  }
  return updated;
}

/**
 * One facet value toggled on or off (issue #267) - the filter panel's own
 * `onChange`, so every facet checkbox shares one implementation of
 * "add/remove a value and invalidate the current page" rather than each
 * re-deriving the parameter name and the add/remove logic.
 *
 * Toggling any filter drops `after`: the population a cursor was paging over
 * no longer exists once the filter set changes underneath it - the same
 * fact `MalformedSearchCursorError`'s `SearchCursorQueryMismatchError`
 * subclass refuses server-side for a search cursor (ADR-0024), applied here
 * before a stale cursor is ever sent.
 */
export function toggleFilterValue<S extends FilteredListSearch>(
  search: S,
  facetKey: string,
  value: string,
): S {
  const paramKey =
    `${FILTER_PARAM_PREFIX}${facetKey}` as `${typeof FILTER_PARAM_PREFIX}${string}`;
  const current = search[paramKey] ?? [];
  const next = current.includes(value)
    ? current.filter((existing) => existing !== value)
    : [...current, value];

  const updated: FilteredListSearch = { ...search };
  delete updated.after;
  if (next.length > 0) {
    updated[paramKey] = next;
  } else {
    delete updated[paramKey];
  }
  return updated as S;
}
