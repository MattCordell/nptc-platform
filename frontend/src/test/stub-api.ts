import { vi } from "vitest";

/**
 * A `fetch` stub dispatching on method and path, so one render can answer
 * the entry read *and* answer a write differently (issue #149).
 *
 * Extracted from `admin-catalogue-edit.test.tsx` (issue #151's own reuse
 * note): the designations, bindings and properties panels all mount under
 * the same admin edit route and need the same "the entry read is fixed,
 * each panel's own writes vary" fixture shape, and a third hand-copied
 * version of this would be the same drift risk `useDebouncedValue`'s
 * extraction was written to avoid.
 */
export interface Route {
  method: string;
  /** Matched against the request path with `endsWith`. */
  path: string;
  status: number;
  body: unknown;
  /**
   * Extra response headers - `Content-Type` is always set below and cannot
   * be overridden here. Added for issue #184's step-up tests, which need a
   * stubbed 403 to carry its own `WWW-Authenticate` challenge.
   */
  headers?: Record<string, string>;
  /**
   * When true the request is recorded but never answered, so the caller's
   * query stays pending for the rest of the test. For asserting a "still
   * checking" state without racing a timer; `status` and `body` are ignored.
   */
  neverSettles?: boolean;
  /**
   * Sends `body` as this content type and as given, instead of as JSON. For a
   * route that answers text, such as the audit export's NDJSON; `body` must
   * then be a string.
   */
  contentType?: string;
}

export interface StubOptions {
  /**
   * Consulted before `routes`, with the number of earlier calls to the same
   * method and path - so one render can answer the same request differently
   * the second time. Return `null` to fall through to `routes`.
   *
   * This rather than re-stubbing `fetch` mid-test: the API client holds the
   * reference it was created with, so a second `vi.stubGlobal` is never seen.
   *
   * `searchParams` carries the request's own query string (PR #285 review
   * round 2): `path` alone is the request URL's pathname, which is identical
   * across e.g. a keyset-paginated route's first and second page - only the
   * query string (`after=...`) tells those two calls apart. Varying on
   * `priorSameCalls` instead works only when a route's *n*th call always
   * means the same thing every render, which a `<StrictMode>` double-fetch
   * of the very first page breaks (issue #267's own `admin-catalogue-list`
   * paging tests hit exactly this before switching to `searchParams`).
   */
  vary?: (
    call: { method: string; path: string; searchParams: URLSearchParams },
    priorSameCalls: number,
  ) => Route | null;
}

/**
 * A fetch stub that dispatches on method and path, so one render can serve
 * the entry read *and* answer a write differently. Returns the calls for
 * assertions on what was actually sent.
 */
export function stubApi(routes: Route[], options: StubOptions = {}) {
  const calls: {
    method: string;
    path: string;
    /** The request's query string, parsed - repeated keys such as
     * `filter.specimen` stay readable through `getAll`. */
    searchParams: URLSearchParams;
    /** The raw URL, for a test that must see exactly what went on the wire
     * (FR-06: a leading-zero code is a string in the path and query too). */
    url: string;
    body: unknown;
    text: string;
  }[] = [];
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    // The raw wire text, alongside the parsed body: `JSON.parse` (like
    // `request.json()`) cannot tell a quoted SCTID from a bare number once
    // parsed, so FR-06's own test needs the text a route actually sent, not
    // what it round-trips back to.
    const text = method === "GET" ? "" : await request.clone().text();
    const body = method === "GET" ? null : JSON.parse(text);
    const priorSameCalls = calls.filter(
      (call) => call.method === method && call.path === path,
    ).length;
    calls.push({
      method,
      path,
      searchParams: url.searchParams,
      url: request.url,
      body,
      text,
    });
    const route =
      options.vary?.({ method, path, searchParams: url.searchParams }, priorSameCalls) ??
      routes.find((r) => r.method === method && path.endsWith(r.path));
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: "no stub" }), { status: 500 });
    }
    if (route.neverSettles === true) {
      return new Promise<Response>(() => {});
    }
    return new Response(
      route.contentType === undefined ? JSON.stringify(route.body) : String(route.body),
      {
        status: route.status,
        // `route.headers` spread first, `Content-Type` set after: the
        // docstring on `Route.headers` promises it cannot be overridden, and
        // an object spread only keeps that promise in this order.
        headers: {
          ...route.headers,
          "Content-Type": route.contentType ?? "application/json",
        },
      },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
