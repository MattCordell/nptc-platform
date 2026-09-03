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
}

export interface StubOptions {
  /**
   * Consulted before `routes`, with the number of earlier calls to the same
   * method and path - so one render can answer the same request differently
   * the second time. Return `null` to fall through to `routes`.
   *
   * This rather than re-stubbing `fetch` mid-test: the API client holds the
   * reference it was created with, so a second `vi.stubGlobal` is never seen.
   */
  vary?: (call: { method: string; path: string }, priorSameCalls: number) => Route | null;
}

/**
 * A fetch stub that dispatches on method and path, so one render can serve
 * the entry read *and* answer a write differently. Returns the calls for
 * assertions on what was actually sent.
 */
export function stubApi(routes: Route[], options: StubOptions = {}) {
  const calls: { method: string; path: string; body: unknown; text: string }[] = [];
  const fetchMock = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
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
    calls.push({ method, path, body, text });
    const route =
      options.vary?.({ method, path }, priorSameCalls) ??
      routes.find((r) => r.method === method && path.endsWith(r.path));
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: "no stub" }), { status: 500 });
    }
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
