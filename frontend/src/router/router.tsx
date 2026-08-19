import { createRouter, type RouterHistory } from "@tanstack/react-router";

import { NotFoundPage } from "../shell/not-found-page.tsx";
import { RouteErrorPage } from "../shell/route-error-page.tsx";
import { routeTree } from "./route-tree.ts";

/**
 * Every search value stays a raw string coming in, and is stringified
 * without quoting going out. Hand-rolled rather than built from the
 * library's `parseSearchWith`/`stringifySearchWith` helpers, because those
 * still delegate to `@tanstack/router-core`'s internal `qss` codec, whose
 * `decode()` calls a `toValue()` step that coerces any numeric-looking
 * string to a real number *before* a custom parser ever runs - passing an
 * identity function as the parser does not intercept this. Concretely:
 * `?page=3` arrives as the NUMBER `3` regardless of `parseSearch`, and a
 * SNOMED code with no leading zero and within `Number.MAX_SAFE_INTEGER`
 * (e.g. `?code=123456`) would too - silently, the exact defect class FR-06
 * exists to eliminate. (An 18-digit SCTID happens to survive by accident:
 * `toValue`'s own round-trip guard rejects it once float rounding changes
 * the digits - but that is luck, not a guarantee, and a leading-zero code
 * would be luck of a different kind.) See `route-tree.test.tsx`'s
 * round-trip assertions, which guard this file.
 */
function parseSearch(searchStr: string): Record<string, unknown> {
  const raw = searchStr[0] === "?" ? searchStr.slice(1) : searchStr;
  const params = new URLSearchParams(raw);
  const result: Record<string, unknown> = {};
  for (const [key, value] of params.entries()) {
    const existing = result[key];
    if (existing === undefined) {
      result[key] = value;
    } else if (Array.isArray(existing)) {
      existing.push(value);
    } else {
      result[key] = [existing, value];
    }
  }
  return result;
}

function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else if (typeof value === "object" && value !== null) {
      params.set(key, JSON.stringify(value));
    } else {
      params.set(key, String(value));
    }
  }
  const str = params.toString();
  return str ? `?${str}` : "";
}

export interface CreateAppRouterOptions {
  /** Injected in tests via `createMemoryHistory` for a cold-session deep link. */
  history?: RouterHistory;
}

export function createAppRouter({ history }: CreateAppRouterOptions = {}) {
  return createRouter({
    routeTree,
    history,

    // Router-level defaults, not per-route: every route inherits the same
    // not-found and error surface, so a new screen cannot forget to wire one
    // (PRD SS17.2 item 5).
    defaultNotFoundComponent: NotFoundPage,
    defaultErrorComponent: RouteErrorPage,
    // "fuzzy" (the default, stated explicitly): the nearest matching
    // ancestor renders the not-found page, so the shell stays on screen and
    // the user has somewhere to go, rather than a blank screen.
    notFoundMode: "fuzzy",

    scrollRestoration: true,

    parseSearch,
    stringifySearch,
  });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
