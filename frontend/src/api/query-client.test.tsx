import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, useMutation, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "./query-client.ts";
import { TERMS_QUERY_KEY } from "./terms.ts";
import { ApiError } from "./unwrap.ts";

/**
 * NFR-45: a write refused for unaccepted terms means the cached
 * `accepted: true` is stale. The cache's own error handler invalidates the
 * terms read so the gate can appear, whichever screen made the write.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function setup() {
  const queryClient = createQueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { invalidate, wrapper };
}

function failingMutation(error: unknown) {
  return () =>
    useMutation({
      mutationFn: () => Promise.reject(error),
    });
}

describe("createQueryClient terms handling", () => {
  it("invalidates the terms read when a mutation is refused for unaccepted terms", async () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(
      failingMutation(
        new ApiError(403, {
          detail: "Accept the terms first.",
          code: "terms_acceptance_required",
        }),
      ),
      { wrapper },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: TERMS_QUERY_KEY });
  });

  it("invalidates the terms read when a query is refused for unaccepted terms", async () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(
      () =>
        useQuery({
          queryKey: ["probe"],
          queryFn: () =>
            Promise.reject(new ApiError(403, { code: "terms_acceptance_required" })),
        }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: TERMS_QUERY_KEY });
  });

  // Principal failure mode: an ordinary 403 for a missing permission shares
  // the status and must not send the user to the gate.
  it("leaves the terms read alone for any other refusal", async () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(
      failingMutation(new ApiError(403, { detail: "Forbidden." })),
      { wrapper },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
