import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { useApiClient } from "./use-api-client.ts";

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
  return <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>;
}

describe("useApiClient", () => {
  it("returns the same client instance across re-renders with a stable auth context", () => {
    const { result, rerender } = renderHook(() => useApiClient(), { wrapper });

    const first = result.current;
    rerender();

    expect(result.current).toBe(first);
  });

  it("throws outside an AuthProvider, matching useAuth's own contract", () => {
    expect(() => renderHook(() => useApiClient())).toThrow(
      /useAuth was called outside an <AuthProvider>/,
    );
  });
});
