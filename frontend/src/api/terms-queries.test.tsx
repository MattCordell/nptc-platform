import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { stubApi } from "../test/stub-api.ts";
import { createQueryClient } from "./query-client.ts";
import { useAcceptTerms, useCurrentTerms } from "./queries.ts";
import { asTermsVersionStale } from "./terms.ts";

/**
 * The terms hooks (NFR-45, NFR-47): the SPA holds no copy of the terms, so
 * every state the gate shows comes from these reads and the refetches the
 * mutation triggers.
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

const TERMS = "/api/v1/auth/terms";
const ACCEPTANCE = "/api/v1/auth/terms/acceptance";
const CURRENT = {
  version: "2026-10-06",
  effective_date: "2026-10-06",
  text: "# Terms of use",
  accepted: false,
};

function wrapperFor(status: AuthContextValue["status"] = "signed-in") {
  const queryClient = createQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={{ ...AUTH, status }}>
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    );
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useCurrentTerms", () => {
  it("reads the terms, version, effective date and accepted flag from the API", async () => {
    const calls = stubApi([{ method: "GET", path: TERMS, status: 200, body: CURRENT }]);

    const { result } = renderHook(() => useCurrentTerms(), { wrapper: wrapperFor() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(CURRENT);
    expect(calls[0]!.path).toBe(TERMS);
  });

  // Principal failure mode (NFR-45): a signed-in user whose session is still
  // restoring must not cache the anonymous `accepted: false` answer.
  it("waits for the session to restore before asking", () => {
    const calls = stubApi([{ method: "GET", path: TERMS, status: 200, body: CURRENT }]);

    const { result } = renderHook(() => useCurrentTerms(), {
      wrapper: wrapperFor("restoring"),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("surfaces a failed read as a query error", async () => {
    stubApi([{ method: "GET", path: TERMS, status: 500, body: { detail: "boom" } }]);

    const { result } = renderHook(() => useCurrentTerms(), { wrapper: wrapperFor() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("does not retry a refusal the server answered", async () => {
    const calls = stubApi([
      { method: "GET", path: TERMS, status: 500, body: { detail: "boom" } },
    ]);

    const { result } = renderHook(() => useCurrentTerms(), { wrapper: wrapperFor() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(calls).toHaveLength(1);
  });

  // A new user's first load sends this read beside GET /auth/me while the
  // user's record is created; one of the two can fail with a response the
  // browser blocks, which arrives as a network error rather than an answer.
  it("retries once when the request fails with no answer, then succeeds", async () => {
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        attempts += 1;
        return attempts === 1
          ? Promise.reject(new TypeError("Failed to fetch"))
          : Promise.resolve(
              new Response(JSON.stringify(CURRENT), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              }),
            );
      }),
    );

    const { result } = renderHook(() => useCurrentTerms(), { wrapper: wrapperFor() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true), { timeout: 4000 });
    expect(attempts).toBe(2);
    expect(result.current.data).toEqual(CURRENT);
  });

  it("gives up after the one retry", async () => {
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        attempts += 1;
        return Promise.reject(new TypeError("Failed to fetch"));
      }),
    );

    const { result } = renderHook(() => useCurrentTerms(), { wrapper: wrapperFor() });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 });
    expect(attempts).toBe(2);
  });
});

describe("useAcceptTerms", () => {
  it("posts the displayed version and refetches the terms on success", async () => {
    const calls = stubApi(
      [
        {
          method: "POST",
          path: ACCEPTANCE,
          status: 200,
          body: { version: CURRENT.version, accepted_at: "2026-10-07T01:00:00Z" },
        },
      ],
      {
        vary: ({ method, path }, priorSameCalls) =>
          method === "GET" && path === TERMS
            ? {
                method,
                path,
                status: 200,
                body: { ...CURRENT, accepted: priorSameCalls > 0 },
              }
            : null,
      },
    );

    const { result } = renderHook(
      () => ({ terms: useCurrentTerms(), accept: useAcceptTerms() }),
      { wrapper: wrapperFor() },
    );
    await waitFor(() => expect(result.current.terms.isSuccess).toBe(true));
    expect(result.current.terms.data?.accepted).toBe(false);

    result.current.accept.mutate(CURRENT.version);

    await waitFor(() => expect(result.current.terms.data?.accepted).toBe(true));
    const post = calls.find((call) => call.method === "POST")!;
    expect(post.body).toEqual({ version: CURRENT.version });
  });

  it("refetches the terms when the server says the version is stale", async () => {
    const calls = stubApi(
      [
        {
          method: "POST",
          path: ACCEPTANCE,
          status: 409,
          body: {
            detail: "The terms have changed.",
            code: "terms_version_stale",
            current_version: "2026-11-01",
          },
        },
      ],
      {
        vary: ({ method, path }, priorSameCalls) =>
          method === "GET" && path === TERMS
            ? {
                method,
                path,
                status: 200,
                body:
                  priorSameCalls > 0 ? { ...CURRENT, version: "2026-11-01" } : CURRENT,
              }
            : null,
      },
    );

    const { result } = renderHook(
      () => ({ terms: useCurrentTerms(), accept: useAcceptTerms() }),
      { wrapper: wrapperFor() },
    );
    await waitFor(() => expect(result.current.terms.isSuccess).toBe(true));

    result.current.accept.mutate(CURRENT.version);

    await waitFor(() => expect(result.current.terms.data?.version).toBe("2026-11-01"));
    expect(asTermsVersionStale(result.current.accept.error)?.current_version).toBe(
      "2026-11-01",
    );
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(2);
  });

  // The terms on screen are still current after any other refusal, so a
  // refetch would only flicker the gate.
  it("does not refetch for any other refusal", async () => {
    const calls = stubApi([
      { method: "GET", path: TERMS, status: 200, body: CURRENT },
      { method: "POST", path: ACCEPTANCE, status: 500, body: { detail: "boom" } },
    ]);

    const { result } = renderHook(
      () => ({ terms: useCurrentTerms(), accept: useAcceptTerms() }),
      { wrapper: wrapperFor() },
    );
    await waitFor(() => expect(result.current.terms.isSuccess).toBe(true));

    result.current.accept.mutate(CURRENT.version);

    await waitFor(() => expect(result.current.accept.isError).toBe(true));
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  });
});
