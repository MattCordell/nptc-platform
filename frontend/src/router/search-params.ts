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
 * `page=` or `sort=` should show the first page, not an error screen.
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
 * Search results are not paginated anywhere near this deep; an upper bound
 * this generous exists only to reject the pathological input
 * (`?page=99999999999999999999`), not to model a real result set.
 */
const MAX_PAGE = 100_000;

/**
 * TanStack Router re-runs `validateSearch` more than once per navigation
 * (e.g. once inside its lightweight route matching, again while building the
 * committed location), and the second call receives this function's own
 * *already-validated* output, not the raw URL string - `page` arrives back
 * as the NUMBER this function itself returned. `validateSearch` must be
 * idempotent (`asPage(asPage(x)) === asPage(x)`), so a real number already in
 * valid range is accepted as-is; only a genuine (string) parse failure falls
 * back to page 1.
 *
 * The string branch requires the *entire* value to be digits
 * (`Number.parseInt` would accept `"3drop"` as `3`, silently swallowing the
 * rest) and rejects anything past `MAX_PAGE` (`Number.parseInt` has no
 * ceiling, so `"99999999999999999999"` would otherwise pass through as a
 * huge, meaningless page number).
 */
function asPage(value: unknown): number {
  if (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 1 &&
    value <= MAX_PAGE
  ) {
    return value;
  }
  const str = asString(value);
  if (!/^\d+$/.test(str)) {
    return 1;
  }
  const parsed = Number(str);
  return parsed >= 1 && parsed <= MAX_PAGE ? parsed : 1;
}

const CATALOGUE_SORTS = ["relevance", "code", "term", "updated"] as const;
export type CatalogueSort = (typeof CATALOGUE_SORTS)[number];

function asSort(value: unknown): CatalogueSort {
  const candidate = asString(value);
  return (CATALOGUE_SORTS as readonly string[]).includes(candidate)
    ? (candidate as CatalogueSort)
    : "relevance";
}

/**
 * Search state for `/catalogue`. Encoded entirely in the URL so a pasted
 * search link reproduces the identical result set and filter state (#140).
 */
export interface CatalogueSearch {
  q: string;
  page: number;
  sort: CatalogueSort;
}

/**
 * What a caller may supply when navigating *to* `/catalogue` - every field
 * optional, so `<Link to="/catalogue">` needs no search prop at all. The
 * `SearchSchemaInput` brand is TanStack Router's mechanism for giving a
 * route a narrower input type than its validated output type; `route-tree.ts`
 * applies it with a type-only cast on `validateCatalogueSearch` when
 * registering the route, so the validator itself keeps a plain, easily
 * unit-tested `Record<string, unknown>` parameter.
 */
export type CatalogueSearchInput = Partial<CatalogueSearch> & SearchSchemaInput;

export function validateCatalogueSearch(
  search: Record<string, unknown>,
): CatalogueSearch {
  return { q: asString(search.q), page: asPage(search.page), sort: asSort(search.sort) };
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

// --- admin catalogue list (issue #267) --------------------------------------

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
 * Search state for `/admin/catalogue/` (issue #267, FR-16, FR-36).
 *
 * Deliberately flat, not `{ q, after, filters: Record<string, string[]> }`:
 * `router.tsx`'s own `stringifySearch` throws for a non-scalar value (a
 * nested object is not "a scalar or an array of scalars"), so a `filters`
 * key holding a record would break the URL round trip the moment there was
 * more than one active facet. Each `filter.<key>` stays its own top-level
 * key, exactly as it appears on the wire (ADR-0032) - `filterSelections`
 * below is what turns this back into a keyed record for a caller that wants
 * one.
 *
 * `after` is a cursor, not a page number (unlike `CatalogueSearch.page`):
 * both `/catalogue/admin/entries` and `/catalogue/admin/search` are
 * keyset-paginated (ADR-0024), so there is no page number to restore, only
 * "the cursor from the last page the caller saw" - see `docs/adr/
 * 0024-catalogue-search-and-pagination.md`.
 */
export type AdminCatalogueSearch = {
  q: string;
  after?: string;
} & {
  [key: `${typeof FILTER_PARAM_PREFIX}${string}`]: string[] | undefined;
};

/**
 * What a caller may supply when navigating *to* `/admin/catalogue/` -
 * matching `CatalogueSearchInput`'s own reasoning (every field optional, a
 * `SearchSchemaInput` brand for TanStack Router's narrower-input mechanism).
 */
export type AdminCatalogueSearchInput = Partial<AdminCatalogueSearch> & SearchSchemaInput;

export function validateAdminCatalogueSearch(
  search: Record<string, unknown>,
): AdminCatalogueSearch {
  const validated: AdminCatalogueSearch = { q: asString(search.q) };
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

/**
 * The `filter.*` entries of a validated admin catalogue search, keyed by
 * facet alone (the `filter.` prefix stripped) - the shape the filter panel
 * and `api/filter-params.ts`'s `filterQueryParams` both want, so neither
 * re-derives it from the flat validated search object by hand.
 */
export function filterSelections(search: AdminCatalogueSearch): Record<string, string[]> {
  const selections: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(search)) {
    if (key.startsWith(FILTER_PARAM_PREFIX) && Array.isArray(value)) {
      selections[key.slice(FILTER_PARAM_PREFIX.length)] = value;
    }
  }
  return selections;
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
export function toggleFilterValue(
  search: AdminCatalogueSearch,
  facetKey: string,
  value: string,
): AdminCatalogueSearch {
  const paramKey =
    `${FILTER_PARAM_PREFIX}${facetKey}` as `${typeof FILTER_PARAM_PREFIX}${string}`;
  const current = search[paramKey] ?? [];
  const next = current.includes(value)
    ? current.filter((existing) => existing !== value)
    : [...current, value];

  const updated: AdminCatalogueSearch = { ...search };
  delete updated.after;
  if (next.length > 0) {
    updated[paramKey] = next;
  } else {
    delete updated[paramKey];
  }
  return updated;
}
