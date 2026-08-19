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
 * TanStack Router re-runs `validateSearch` more than once per navigation
 * (e.g. once inside its lightweight route matching, again while building the
 * committed location), and the second call receives this function's own
 * *already-validated* output, not the raw URL string - `page` arrives back
 * as the NUMBER this function itself returned. `validateSearch` must be
 * idempotent (`asPage(asPage(x)) === asPage(x)`), so a real number in valid
 * range is accepted as-is; only a genuine (string) parse failure falls back
 * to page 1.
 */
function asPage(value: unknown): number {
  if (typeof value === "number" && Number.isInteger(value) && value >= 1) {
    return value;
  }
  const parsed = Number.parseInt(asString(value), 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
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

export type SignInSearchInput = SignInSearch & SearchSchemaInput;

export function validateSignInSearch(search: Record<string, unknown>): SignInSearch {
  const redirect = asString(search.redirect);
  return redirect ? { redirect } : {};
}
