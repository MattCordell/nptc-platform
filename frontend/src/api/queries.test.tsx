import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { createQueryClient } from "./query-client.ts";
import { useEntriesList, useEntryDetail } from "./queries.ts";

/**
 * TanStack Query hooks over the generated client (issue #147).
 *
 * Infrastructure-only wiring - these tests prove the hooks call the right
 * path with the right params and surface a failed response as a thrown
 * error for `useQuery` to catch, not that any page renders the result yet.
 */

const AUTH: AuthContextValue = {
  status: "signed-in",
  getAccessToken: () => Promise.resolve("test-token"),
  signIn: () => Promise.resolve(),
  signOut: () => Promise.resolve(),
  register: () => Promise.resolve(),
  restore: () => Promise.resolve(),
  completeCallback: () => Promise.resolve(null),
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = createQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>((request) => {
    void request;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useEntriesList", () => {
  it("fetches the entries page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useEntriesList({ limit: 20 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/entries");
    expect(requestUrl.searchParams.get("limit")).toBe("20");
  });

  it("surfaces a non-2xx response as a query error", async () => {
    stubFetch(401, { detail: "not authenticated" });

    const { result } = renderHook(() => useEntriesList(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe("useEntryDetail", () => {
  it("fetches the entry by business key", async () => {
    const entry = { business_key: "NPTC-000247", preferred_term: "x", length: 1 };
    const fetchMock = stubFetch(200, entry);

    const { result } = renderHook(() => useEntryDetail("NPTC-000247"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(entry);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/entries/NPTC-000247");
  });

  it("does not fetch for a blank business key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useEntryDetail(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
