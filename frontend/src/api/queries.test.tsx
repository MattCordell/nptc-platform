import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { asCollisionError, asVersionConflict } from "./conflicts.ts";
import { stubApi } from "../test/stub-api.ts";
import { createQueryClient } from "./query-client.ts";
import {
  type CatalogueSearchParams,
  useAcknowledgeCollision,
  useAddDesignations,
  useAdminEntriesList,
  useAdminEntryDetail,
  useAdminSearch,
  useAmendDesignation,
  useCatalogueSearch,
  useEntriesList,
  useEntryBindings,
  useEntryByCode,
  useEntryBySystemCode,
  useEntryDesignations,
  useEntryDetail,
  useEntryHistory,
  useEntryProperties,
  usePatchEntryCore,
  usePropertyDefinition,
  usePropertyDefinitions,
  usePropertyValueOptions,
  useReinstateDesignation,
  useRetireDesignation,
  useSavePropertyValues,
  useSession,
} from "./queries.ts";
import type { ApiError } from "./unwrap.ts";

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
  stepUp: () => Promise.resolve("interaction-required"),
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

describe("useSession", () => {
  it("exposes mfa_satisfied from /auth/me for the pre-emptive step-up banner", async () => {
    const session = {
      authenticated: true,
      user: {
        username: "a.curator",
        display_name: "A Curator",
        organisation: null,
        status: "active",
      },
      roles: ["Administrator"],
      permissions: ["role.grant_member"],
      mfa_satisfied: false,
    };
    const fetchMock = stubFetch(200, session);

    const { result } = renderHook(() => useSession(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(session);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/auth/me");
  });

  // The route's own docstring: never 401s for an anonymous caller. This
  // hook must not gate the request on any prior knowledge of auth status.
  it("fetches even for an anonymous caller", async () => {
    const fetchMock = stubFetch(200, {
      authenticated: false,
      user: null,
      roles: [],
      permissions: [],
      mfa_satisfied: false,
    });

    const { result } = renderHook(() => useSession(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.data?.authenticated).toBe(false);
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

describe("useAdminEntriesList", () => {
  it("fetches the admin entries page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useAdminEntriesList({ limit: 20 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/admin/entries");
    expect(requestUrl.searchParams.get("limit")).toBe("20");
  });

  // Issue #276/ADR-0032: the one place a `filter.<key>` parameter name is
  // built by hand for the generated client - this proves it actually reaches
  // the wire as a repeated parameter, not a single comma-joined one.
  it("sends each selected filter value as its own repeated filter.<key> parameter", async () => {
    const fetchMock = stubFetch(200, { items: [], next_cursor: null });

    renderHook(() => useAdminEntriesList({ filters: { status: ["draft", "active"] } }), {
      wrapper,
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.searchParams.getAll("filter.status")).toEqual(["draft", "active"]);
  });

  it("does not fetch when disabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminEntriesList({ enabled: false }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useAdminSearch", () => {
  it("fetches the admin search page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null, facets: [] };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useAdminSearch({ q: "glucose" }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/admin/search");
    expect(requestUrl.searchParams.get("q")).toBe("glucose");
  });

  it("does not fetch for a blank query, even when enabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "" }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  // PR #285 review finding 5: trimmed, not merely non-empty, so this can
  // never fire in a state the page's own `mode` considers browse (it
  // computes `mode` with the identical `.trim()`).
  it("does not fetch for a whitespace-only query, even when enabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "   " }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not fetch when disabled, even with a non-blank query", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "glucose", enabled: false }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useAddDesignations", () => {
  it("posts the split terms as a batch and invalidates the entry", async () => {
    const fetchMock = stubFetch(201, { designations: [], warnings: [], row_version: 2 });
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
      expected_row_version: 1,
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
      expected_row_version: 1,
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
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asCollisionError(result.current.error)?.collisions[0]?.business_key).toBe(
      "NPTC-000111",
    );
  });

  // FR-38 (issue #300): the second axis this route can now refuse on. The
  // cached entry must be refetched, matching useAmendDesignation/
  // useSavePropertyValues - otherwise a retry from the same open form fails
  // identically against the same stale row_version.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useAddDesignations("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      terms: ["Zovirax"],
      use: "synonym",
      reason: "Add under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
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
    const fetchMock = stubFetch(200, {
      designation: { term: "Cyclir", status: "retired" },
      row_version: 2,
    });
    const { result } = renderHook(() => useRetireDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      term: "Cyclir",
      reason: "Withdrawn brand name",
      expected_row_version: 1,
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
      expected_row_version: 1,
    });
  });

  // FR-38 (issue #300): this route now takes a lock token and can refuse on
  // it - see useAddDesignations' identical test for why the cache must be
  // refetched, not left stale.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useRetireDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      language: "en-AU",
      term: "Cyclir",
      reason: "Retire under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
  });
});

describe("useReinstateDesignation", () => {
  it("posts the term and its mandatory reason to the reinstatement route", async () => {
    const fetchMock = stubFetch(200, {
      designation: { term: "Cyclir", status: "active" },
      warnings: [],
      row_version: 2,
    });
    const { result } = renderHook(() => useReinstateDesignation("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      language: "en-AU",
      term: "Cyclir",
      reason: "Retired by mistake",
      expected_row_version: 1,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/reinstatement",
    );
    expect(await bodyOf(request)).toEqual({
      language: "en-AU",
      term: "Cyclir",
      reason: "Retired by mistake",
      expected_row_version: 1,
    });
  });

  // FR-38 (issue #300): this route takes a lock token too, and can refuse on
  // it - see useRetireDesignation's identical test for why the cache must be
  // refetched, not left stale.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useReinstateDesignation("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      language: "en-AU",
      term: "Cyclir",
      reason: "Reinstate under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
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

/**
 * The registry-properties hooks (issue #151) - generated-form data plus the
 * two writes the panel needs (a property's values, and the entry's own core
 * columns). Same infrastructure-only scope as the write hooks above.
 */

describe("usePropertyDefinitions", () => {
  it("always asks for deprecated properties too, so FR-11 values still render", async () => {
    const list = { items: [] };
    const fetchMock = stubFetch(200, list);

    const { result } = renderHook(() => usePropertyDefinitions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(list);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/registry/properties");
    expect(requestUrl.searchParams.get("include_deprecated")).toBe("true");
  });
});

describe("usePropertyDefinition", () => {
  it("fetches one definition by key", async () => {
    const definition = { key: "specimen", label: "Specimen", status: "deprecated" };
    const fetchMock = stubFetch(200, definition);

    const { result } = renderHook(() => usePropertyDefinition("specimen"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(definition);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/registry/properties/specimen",
    );
  });

  it("surfaces a 404 as an error carrying its status", async () => {
    stubFetch(404, { detail: "No property definition matches the given key." });

    const { result } = renderHook(() => usePropertyDefinition("missing"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("does not fetch for a blank key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => usePropertyDefinition(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("usePropertyValueOptions", () => {
  it("fetches a coded property's offerable values by key", async () => {
    const page = { items: [{ code: "119361006", display: "Plasma specimen" }], total: 1 };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => usePropertyValueOptions("specimen", ""), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/registry/properties/specimen/values",
    );
  });

  it("passes a non-empty filter through as a query param", async () => {
    const fetchMock = stubFetch(200, { items: [], total: 0 });

    renderHook(() => usePropertyValueOptions("specimen", "plasma"), { wrapper });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(new URL(requestFor(fetchMock).url).searchParams.get("filter")).toBe("plasma");
  });

  it("does not fetch for a blank property key", () => {
    const fetchMock = stubFetch(200, { items: [], total: 0 });

    renderHook(() => usePropertyValueOptions("", ""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useSavePropertyValues", () => {
  it("PUTs the whole value set to the property's own route and invalidates the entry", async () => {
    const fetchMock = stubFetch(200, {
      values: [{ key: "discipline", label: "Discipline", ordinal: 0, value: "HAEM" }],
      row_version: 4,
    });
    const { result } = renderHook(
      () => ({
        save: useSavePropertyValues("NPTC-000247", "discipline"),
        entry: useAdminEntryDetail("NPTC-000247"),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.entry.isSuccess).toBe(true));

    result.current.save.mutate({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });
    await waitFor(() => expect(result.current.save.isSuccess).toBe(true));

    const request = requestFor(fetchMock, 1);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/properties/discipline",
    );
    expect(request.method).toBe("PUT");
    expect(await bodyOf(request)).toEqual({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3));
    expect(new URL(requestFor(fetchMock, 2).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  // Principal failure mode: FR-38's optimistic lock. The cached entry must be
  // refetched, matching useAmendDesignation - otherwise a retry from the same
  // open dialog fails identically against the same stale row_version.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(
      () => useSavePropertyValues("NPTC-000247", "discipline"),
      { wrapper },
    );

    result.current.mutate({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(4);
  });
});

describe("usePatchEntryCore", () => {
  it("PATCHes the entry's own status/specimen_unconstrained columns", async () => {
    const fetchMock = stubFetch(200, {
      status: "active",
      specimen_unconstrained: true,
      row_version: 4,
    });
    const { result } = renderHook(() => usePatchEntryCore("NPTC-000247"), { wrapper });

    result.current.mutate({
      specimen_unconstrained: true,
      reason: "This entry accepts any specimen",
      expected_row_version: 3,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe("/api/v1/catalogue/entries/NPTC-000247");
    expect(request.method).toBe("PATCH");
    expect(await bodyOf(request)).toEqual({
      specimen_unconstrained: true,
      reason: "This entry accepts any specimen",
      expected_row_version: 3,
    });
  });

  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => usePatchEntryCore("NPTC-000247"), { wrapper });

    result.current.mutate({
      specimen_unconstrained: true,
      reason: "This entry accepts any specimen",
      expected_row_version: 3,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(4);
  });
});

/**
 * The public catalogue read hooks (FR-14 to FR-19). These use the shared
 * `stubApi` so each test can assert the query string the request carried,
 * not only its path.
 */

const ENTRY_KEY = "NPTC-000247";
const ENTRIES = "/api/v1/catalogue/entries";

function wrapperWithStatus(
  status: AuthContextValue["status"],
  queryClient = createQueryClient(),
) {
  return function StatusWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={{ ...AUTH, status }}>
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    );
  };
}

describe("useEntriesList filters and gating", () => {
  it("sends each facet selection as a repeated filter.<key> pair", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(
      () =>
        useEntriesList({
          filters: { specimen: ["119297000", "122575003"], discipline: ["chem"] },
        }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const { searchParams } = calls[0]!;
    expect(searchParams.getAll("filter.specimen")).toEqual(["119297000", "122575003"]);
    expect(searchParams.getAll("filter.discipline")).toEqual(["chem"]);
  });

  it("passes the after cursor through unchanged and sends no offset, page or total", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(
      () => useEntriesList({ limit: 10, after: "opaque+/=cursor" }),
      {
        wrapper,
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const { searchParams } = calls[0]!;
    expect(searchParams.get("after")).toBe("opaque+/=cursor");
    expect([...searchParams.keys()].sort()).toEqual(["after", "limit"]);
  });

  it("sends no after parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntriesList({ after: null }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("after")).toBe(false);
  });

  it("does not fetch while enabled is false", () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntriesList({ enabled: false }), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useCatalogueSearch", () => {
  const SEARCH = "/api/v1/catalogue/search";

  it("sends q, the cursor and repeated filters, and exposes the parsed page", async () => {
    const page = { items: [], facets: [], next_cursor: null };
    const calls = stubApi([{ method: "GET", path: SEARCH, status: 200, body: page }]);

    const { result } = renderHook(
      () =>
        useCatalogueSearch({
          q: "glucose",
          limit: 25,
          after: "cursor-1",
          filters: { specimen: ["119297000", "122575003"] },
        }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(page);
    const call = calls[0]!;
    expect(call.path).toBe(SEARCH);
    expect(call.searchParams.get("q")).toBe("glucose");
    expect(call.searchParams.get("limit")).toBe("25");
    expect(call.searchParams.get("after")).toBe("cursor-1");
    expect(call.searchParams.getAll("filter.specimen")).toEqual([
      "119297000",
      "122575003",
    ]);
    expect(
      [...call.searchParams.keys()].some((key) => /^(offset|page|total)/.test(key)),
    ).toBe(false);
  });

  it("keeps a different q, cursor or filter set in a separate cache entry", async () => {
    const calls = stubApi([
      { method: "GET", path: SEARCH, status: 200, body: { items: [], facets: [] } },
    ]);
    const queryClient = createQueryClient();
    const { rerender } = renderHook(
      (props: CatalogueSearchParams) => useCatalogueSearch(props),
      {
        wrapper: wrapperWithStatus("signed-in", queryClient),
        initialProps: { q: "glucose" } as CatalogueSearchParams,
      },
    );
    await waitFor(() => expect(calls).toHaveLength(1));

    rerender({ q: "glucose", after: "cursor-1" });
    await waitFor(() => expect(calls).toHaveLength(2));

    rerender({ q: "glucose", after: "cursor-1", filters: { specimen: ["119297000"] } });
    await waitFor(() => expect(calls).toHaveLength(3));

    rerender({ q: "glucose", after: "cursor-1", filters: { specimen: ["122575003"] } });
    await waitFor(() => expect(calls).toHaveLength(4));

    rerender({ q: "sodium", after: "cursor-1", filters: { specimen: ["122575003"] } });
    await waitFor(() => expect(calls).toHaveLength(5));
  });

  // A page's next_cursor is `string | null`, so a screen passes it straight
  // back; a null on the last page must mean "no cursor", not the text "null".
  it("sends no after parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: SEARCH, status: 200, body: { items: [], facets: [] } },
    ]);

    const { result } = renderHook(
      () => useCatalogueSearch({ q: "glucose", after: null }),
      {
        wrapper,
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("after")).toBe(false);
  });

  it.each(["", "   "])("does not fetch for a blank q (%j)", (q) => {
    const calls = stubApi([{ method: "GET", path: SEARCH, status: 200, body: {} }]);

    const { result } = renderHook(() => useCatalogueSearch({ q }), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("surfaces a non-2xx response as a query error", async () => {
    stubApi([
      { method: "GET", path: SEARCH, status: 422, body: { detail: "bad cursor" } },
    ]);

    const { result } = renderHook(() => useCatalogueSearch({ q: "glucose" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(422);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useCatalogueSearch({ q: "glucose" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });
});

describe.each([
  { name: "useEntryDesignations", hook: useEntryDesignations, suffix: "designations" },
  { name: "useEntryBindings", hook: useEntryBindings, suffix: "bindings" },
  { name: "useEntryProperties", hook: useEntryProperties, suffix: "properties" },
])("$name", ({ hook, suffix }) => {
  const path = `${ENTRIES}/${ENTRY_KEY}/${suffix}`;

  it("fetches the entry's records and exposes the parsed body", async () => {
    const body = { items: [{ marker: suffix }] };
    const calls = stubApi([{ method: "GET", path, status: 200, body }]);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);
    expect(calls[0]!.path).toBe(path);
    expect([...calls[0]!.searchParams.keys()]).toEqual([]);
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([{ method: "GET", path, status: 404, body: { detail: "no such entry" } }]);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it("does not fetch for a blank business key", () => {
    const calls = stubApi([{ method: "GET", path, status: 200, body: {} }]);

    const { result } = renderHook(() => hook(""), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useEntryHistory", () => {
  const HISTORY = `${ENTRIES}/${ENTRY_KEY}/history`;

  it("sends limit and the before cursor, and exposes the parsed page", async () => {
    const page = { items: [], next_cursor: null };
    const calls = stubApi([{ method: "GET", path: HISTORY, status: 200, body: page }]);

    const { result } = renderHook(
      () => useEntryHistory(ENTRY_KEY, { limit: 5, before: "cursor-9" }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(page);
    const { searchParams } = calls[0]!;
    expect(searchParams.get("limit")).toBe("5");
    expect(searchParams.get("before")).toBe("cursor-9");
    expect([...searchParams.keys()].sort()).toEqual(["before", "limit"]);
  });

  it("sends no before parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY, { before: null }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("before")).toBe(false);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it("does not fetch for a blank business key", () => {
    const calls = stubApi([{ method: "GET", path: HISTORY, status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryHistory(""), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  // Principal failure mode (NFR-26): the route answers `changed_by: null` to
  // an anonymous caller. A page cached before the session restored must not
  // be served to the signed-in reader afterwards.
  it("waits for the session to restore before asking", () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY), {
      wrapper: wrapperWithStatus("restoring"),
    });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("asks again, rather than reusing the cached page, when the caller signs in", async () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);
    const queryClient = createQueryClient();
    let status: AuthContextValue["status"] = "signed-out";
    function Wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <AuthContext.Provider value={{ ...AUTH, status }}>
            {children}
          </AuthContext.Provider>
        </QueryClientProvider>
      );
    }

    const { result, rerender } = renderHook(() => useEntryHistory(ENTRY_KEY), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls).toHaveLength(1);

    status = "signed-in";
    rerender();

    await waitFor(() => expect(calls).toHaveLength(2));
  });
});

describe("useEntryByCode", () => {
  it("fetches the entry for a code under a system token", async () => {
    const entry = { business_key: ENTRY_KEY };
    const calls = stubApi([
      {
        method: "GET",
        path: "/api/v1/catalogue/code/sct/26604007",
        status: 200,
        body: entry,
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "26604007"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(entry);
    expect(calls[0]!.path).toBe("/api/v1/catalogue/code/sct/26604007");
  });

  // FR-06: a number-typed code would lose the leading zeros. The assertion is
  // on the raw request URL, which a coerced value could not fake.
  it("keeps a code with leading zeros as written", async () => {
    const calls = stubApi([
      {
        method: "GET",
        path: "/code/sct/0012345",
        status: 200,
        body: { business_key: ENTRY_KEY },
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "0012345"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(new URL(calls[0]!.url).pathname.endsWith("/code/sct/0012345")).toBe(true);
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([
      {
        method: "GET",
        path: "/code/sct/999",
        status: 404,
        body: { detail: "No such entry." },
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "999"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryByCode("sct", "26604007"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it.each([
    ["a blank token", "", "26604007"],
    ["a blank code", "sct", ""],
  ])("does not fetch for %s", (_label, token, code) => {
    const calls = stubApi([{ method: "GET", path: "/code/", status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryByCode(token, code), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useEntryBySystemCode", () => {
  const LOOKUP = "/api/v1/catalogue/lookup";
  const SNOMED = "http://snomed.info/sct";

  it("sends the system URI and code as query parameters", async () => {
    const entry = { business_key: ENTRY_KEY };
    const calls = stubApi([{ method: "GET", path: LOOKUP, status: 200, body: entry }]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "26604007"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(entry);
    expect(calls[0]!.path).toBe(LOOKUP);
    expect(calls[0]!.searchParams.get("system")).toBe(SNOMED);
    expect(calls[0]!.searchParams.get("code")).toBe("26604007");
  });

  it("keeps a code with leading zeros as written", async () => {
    const calls = stubApi([
      { method: "GET", path: LOOKUP, status: 200, body: { business_key: ENTRY_KEY } },
    ]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "0012345"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(new URL(calls[0]!.url).search).toContain("code=0012345");
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([
      { method: "GET", path: LOOKUP, status: 404, body: { detail: "No such entry." } },
    ]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "999"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "26604007"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it.each([
    ["a blank system", "", "26604007"],
    ["a blank code", SNOMED, ""],
  ])("does not fetch for %s", (_label, system, code) => {
    const calls = stubApi([{ method: "GET", path: LOOKUP, status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryBySystemCode(system, code), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});
