import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { asCollisionError, asVersionConflict } from "./conflicts.ts";
import { createQueryClient } from "./query-client.ts";
import {
  useAcknowledgeCollision,
  useAddDesignations,
  useAdminEntryDetail,
  useAmendDesignation,
  useEntriesList,
  useEntryDetail,
  useRetireDesignation,
} from "./queries.ts";

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

// A response with no body at all - distinct from stubFetch's JSON body.
// openapi-fetch parses `error: undefined` for this shape (dist/index.mjs),
// which is exactly the case unwrap() (frontend/src/api/unwrap.ts) exists to
// still treat as a failure.
function stubFetchEmptyBody(status: number) {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>((request) => {
    void request;
    return Promise.resolve(new Response(null, { status }));
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

  // Principal failure mode (issue #147 review): a failed response with an
  // empty body parses as `error: undefined` in openapi-fetch, so a hook
  // that branched on the parsed error alone would resolve this as a
  // *successful* empty result instead of an error.
  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntriesList(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
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

/**
 * The write hooks (issue #149) - the first mutations in this app.
 *
 * What these prove is the wiring: the right path, the body passed through
 * unaltered, a refusal thrown rather than swallowed, and the admin-detail
 * query invalidated so the screen re-reads instead of guessing at the new
 * state. What the screen does with any of that is the page's own tests.
 */

function requestFor(fetchMock: ReturnType<typeof stubFetch>, call = 0) {
  return fetchMock.mock.calls[call]?.[0] as Request;
}

async function bodyOf(request: Request) {
  return (await request.json()) as Record<string, unknown>;
}

describe("useAdminEntryDetail", () => {
  it("reads the admin route, which serves an entry of any status", async () => {
    // The whole reason this hook exists rather than useEntryDetail: the
    // public route 404s a draft entry, so an edit screen cannot load its own
    // subject through it (issue #228).
    const entry = { business_key: "NPTC-000247", status: "draft", row_version: 3 };
    const fetchMock = stubFetch(200, entry);

    const { result } = renderHook(() => useAdminEntryDetail("NPTC-000247"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(entry);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  it("does not fetch for a blank business key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminEntryDetail(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useAddDesignations", () => {
  it("posts the split terms as a batch and invalidates the entry", async () => {
    const fetchMock = stubFetch(201, { designations: [], warnings: [] });
    const { result } = renderHook(
      () => ({
        add: useAddDesignations("NPTC-000247"),
        entry: useAdminEntryDetail("NPTC-000247"),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.entry.isSuccess).toBe(true));

    result.current.add.mutate({
      language: "en-AU",
      terms: ["Zovirax", "Cyclir"],
      use: "synonym",
      reason: "Split the pasted synonym cell",
    });
    await waitFor(() => expect(result.current.add.isSuccess).toBe(true));

    const request = requestFor(fetchMock, 1);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations",
    );
    expect(request.method).toBe("POST");
    expect(await bodyOf(request)).toEqual({
      language: "en-AU",
      terms: ["Zovirax", "Cyclir"],
      use: "synonym",
      reason: "Split the pasted synonym cell",
    });
    // The invalidation: a third call, re-reading the entry.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3));
    expect(new URL(requestFor(fetchMock, 2).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  // The principal failure mode: an FR-05 error-severity collision. It must
  // reach the caller as an error with its body intact - a mutation that
  // resolved successfully here would let the screen report a save that never
  // happened.
  it("throws a 409 collision with its body intact", async () => {
    stubFetch(409, {
      detail: "This term matches another entry's preferred term or synonym.",
      collisions: [
        { severity: "error", business_key: "NPTC-000111", preferred_term: "Adrenal Ab" },
      ],
    });
    const { result } = renderHook(() => useAddDesignations("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      terms: ["Adrenal Ab"],
      use: "synonym",
      reason: "Add a colliding synonym",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asCollisionError(result.current.error)?.collisions[0]?.business_key).toBe(
      "NPTC-000111",
    );
  });
});

describe("useAmendDesignation", () => {
  it("sends use and expected_row_version so the entry's own term is addressable", async () => {
    // ADR-0022 keeps the catalogue's own en-AU preferred term off `designation`
    // entirely. `use: "preferred"` is what reaches past a synonym shadowing it,
    // and `expected_row_version` is FR-38's lock, required on that branch.
    const fetchMock = stubFetch(200, {
      designation: { term: "Serum ferritin", use: "preferred", language: "en-AU" },
      warnings: [],
      row_version: 4,
    });
    const { result } = renderHook(() => useAmendDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      term: "Ferritin",
      new_term: "Serum ferritin",
      use: "preferred",
      expected_row_version: 3,
      reason: "Disambiguate against the plasma assay",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/amendment",
    );
    expect(await bodyOf(request)).toMatchObject({
      term: "Ferritin",
      new_term: "Serum ferritin",
      use: "preferred",
      expected_row_version: 3,
    });
  });

  it("throws a 409 version conflict with its body intact", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useAmendDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      term: "Ferritin",
      new_term: "Serum ferritin",
      expected_row_version: 3,
      reason: "Amend under a stale version",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(4);
  });
});

describe("useRetireDesignation", () => {
  it("posts the term and its mandatory reason to the retirement route", async () => {
    const fetchMock = stubFetch(200, { term: "Cyclir", status: "retired" });
    const { result } = renderHook(() => useRetireDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      term: "Cyclir",
      reason: "Withdrawn brand name",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/retirement",
    );
    expect(await bodyOf(request)).toEqual({
      language: "en-AU",
      term: "Cyclir",
      reason: "Withdrawn brand name",
    });
  });
});

describe("useAcknowledgeCollision", () => {
  it("posts to the acknowledgement route, which is gated on another permission", async () => {
    // `validation.acknowledge`, held by Reviewer as well as Administrator and
    // not MFA-gated - so a caller who can edit is not guaranteed to be able to
    // acknowledge, and this route's 403 is a different sentence.
    const fetchMock = stubFetch(200, {
      language: "en-AU",
      reason: "Both are valid",
      created: true,
    });
    const { result } = renderHook(() => useAcknowledgeCollision("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      language: "en-AU",
      term: "Ferritin",
      reason: "Both are valid",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/acknowledgement",
    );
    expect(result.current.data).toMatchObject({ created: true });
  });
});
