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

/**
 * Only scalars and arrays of scalars are supported - deliberately, not as an
 * oversight. `parseSearch` above never JSON-parses a value back out (that is
 * the whole point: a value is never coerced), so a plain object would
 * `JSON.stringify` cleanly going out but come back on the next parse as an
 * unparsed JSON string, not the original object - `stringifySearch` and
 * `parseSearch` would silently stop being inverses of each other. Throwing
 * here (a route's `validateSearch` output should never contain one) is
 * cheaper than discovering that gap the day someone adds the first
 * structured filter param and wonders why it doesn't round-trip.
 */
function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== null && typeof item === "object") {
          throw new Error(
            `stringifySearch: array value for "${key}" contains a non-scalar item - ` +
              "only scalars and arrays of scalars are supported (router.tsx).",
          );
        }
        params.append(key, String(item));
      }
    } else if (value !== null && typeof value === "object") {
      throw new Error(
        `stringifySearch: value for "${key}" is a non-scalar object - only scalars ` +
          "and arrays of scalars are supported (router.tsx).",
      );
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
