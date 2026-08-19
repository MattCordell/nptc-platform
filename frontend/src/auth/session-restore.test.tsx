import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "./session.ts";
import { SessionRestore } from "./session-restore.tsx";

/**
 * The cold-load session probe (issue #41, ADR-0021).
 *
 * Tokens live in memory only, so a page reload starts with no session even
 * when the Keycloak SSO cookie is still valid. Without this, every refresh
 * would look like a sign-out.
 */

function renderWith(restore: () => Promise<void>) {
  const value = {
    status: "signed-out",
    getAccessToken: () => Promise.resolve(null),
    signIn: () => Promise.resolve(),
    signOut: () => Promise.resolve(),
    register: () => Promise.resolve(),
    restore,
    completeCallback: () => Promise.resolve(null),
  } satisfies AuthContextValue;

  return render(
    <AuthContext.Provider value={value}>
      <SessionRestore />
    </AuthContext.Provider>,
  );
}

describe("SessionRestore", () => {
  it("attempts a restore on mount", () => {
    const restore = vi.fn(() => Promise.resolve());

    renderWith(restore);

    expect(restore).toHaveBeenCalledTimes(1);
  });

  it("attempts it only once, even though StrictMode mounts twice", () => {
    // Two probes would race for the same single-use transaction slot, and
    // the second would overwrite the first's `state`.
    const restore = vi.fn(() => Promise.resolve());

    render(
      <AuthContext.Provider
        value={{
          status: "signed-out",
          getAccessToken: () => Promise.resolve(null),
          signIn: () => Promise.resolve(),
          signOut: () => Promise.resolve(),
          register: () => Promise.resolve(),
          restore,
          completeCallback: () => Promise.resolve(null),
        }}
      >
        <SessionRestore />
      </AuthContext.Provider>,
    );

    expect(restore).toHaveBeenCalledTimes(1);
  });

  it("renders nothing", () => {
    const { container } = renderWith(() => Promise.resolve());

    expect(container).toBeEmptyDOMElement();
  });
});
