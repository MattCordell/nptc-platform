import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./auth-context.tsx";
import type { AuthConfig } from "./config.ts";
import { resetEndpointCache } from "./discovery.ts";
import { InteractionRequiredError } from "./flow.ts";
import { useAuth, type AuthContextValue } from "./session.ts";
import { clearTransaction } from "./transaction.ts";

/**
 * `AuthProvider` - the in-memory session itself (issue #41, ADR-0021).
 *
 * The iframe renewal is injected rather than exercised: jsdom performs no
 * real navigation, so the real one can only ever hit its own timeout. What
 * is tested here is everything the provider decides *around* it - when it
 * renews, what it does with a refusal, and what it clears on sign-out.
 */

const ISSUER = "https://idp.test/realms/nptc";

const CONFIG: AuthConfig = {
  issuer: ISSUER,
  clientId: "nptc-frontend",
  redirectUri: "https://app.test/auth/callback",
  postLogoutRedirectUri: "https://app.test",
};

const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
  token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
  end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
};

let assigned: string[] = [];

/**
 * Surfaces the context so assertions can drive it directly. A mutable
 * holder rather than a reassigned module variable, which
 * `react-hooks/globals` forbids inside a component (a render must not write
 * to module scope).
 */
const held: { api: AuthContextValue | null } = { api: null };

function api(): AuthContextValue {
  if (!held.api) {
    throw new Error("the provider has not rendered yet");
  }
  return held.api;
}

function Probe() {
  const value = useAuth();
  // Published from an effect, not during render: writing to module scope
  // while rendering is what `react-hooks/immutability` forbids, and React
  // may discard a render it never commits.
  useEffect(() => {
    held.api = value;
  }, [value]);
  return <span data-testid="status">{value.status}</span>;
}

const navigate = (url: string) => {
  assigned.push(url);
};

function renderProvider(silentAuthorize = vi.fn()) {
  return render(
    <AuthProvider config={CONFIG} silentAuthorize={silentAuthorize} navigate={navigate}>
      <Probe />
    </AuthProvider>,
  );
}

/** A silent renewal that succeeds, standing in for a live SSO session. */
function succeedingRenewal() {
  return vi.fn((url: string) => {
    const state = new URL(url).searchParams.get("state") ?? "";
    return Promise.resolve(new URLSearchParams({ code: "silent-code", state }));
  });
}

beforeEach(() => {
  resetEndpointCache();
  clearTransaction();
  window.sessionStorage.clear();
  assigned = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(".well-known")) {
        return Promise.resolve(
          new Response(JSON.stringify(DISCOVERY), {
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: "access-token",
            id_token: "id-token",
            expires_in: 300,
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
      );
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("initial state", () => {
  it("starts signed out, having made no network call of its own", () => {
    renderProvider();

    expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
    // The provider must not probe on mount - `SessionRestore` decides that,
    // so a test (or a screen) can mount it without a surprise round trip.
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports unavailable when there is no configuration to use", () => {
    // Real deployments that forgot the VITE_* variables must render a shell
    // saying sign-in is unavailable, not crash on the first render.
    render(
      <AuthProvider config={undefined} silentAuthorize={vi.fn()} navigate={navigate}>
        <Probe />
      </AuthProvider>,
    );

    expect(screen.getByTestId("status")).toHaveTextContent("unavailable");
  });
});

describe("restore", () => {
  it("becomes signed in when the SSO session is still alive", async () => {
    renderProvider(succeedingRenewal());

    await act(async () => {
      await api().restore();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("signed-in");
  });

  it("stays signed out, quietly, when interaction is required", async () => {
    // The post-logout path. This must not surface as an error: being
    // signed out is the correct answer, not a fault.
    const renewal = vi.fn(() =>
      Promise.reject(new InteractionRequiredError("login_required")),
    );
    renderProvider(renewal);

    await act(async () => {
      await api().restore();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
  });

  it("reports unavailable when the provider cannot be reached at all", async () => {
    // Distinct from "signed out": the user cannot fix this by signing in,
    // so `RequireAuth` must not bounce them into a redirect loop.
    const renewal = vi.fn(() => Promise.reject(new Error("network down")));
    renderProvider(renewal);

    await act(async () => {
      await api().restore();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("unavailable");
  });
});

describe("getAccessToken", () => {
  it("returns null when signed out", async () => {
    const renewal = vi.fn(() =>
      Promise.reject(new InteractionRequiredError("login_required")),
    );
    renderProvider(renewal);

    await act(async () => {
      await expect(api().getAccessToken()).resolves.toBeNull();
    });
  });

  it("reuses a token that is not near expiry", async () => {
    const renewal = succeedingRenewal();
    renderProvider(renewal);

    await act(async () => {
      await api().restore();
    });
    await act(async () => {
      await expect(api().getAccessToken()).resolves.toBe("access-token");
    });

    // One renewal, from `restore` - the second call must not trigger another.
    expect(renewal).toHaveBeenCalledTimes(1);
  });

  it("renews once for concurrent callers, not once each", async () => {
    const renewal = succeedingRenewal();
    renderProvider(renewal);

    await act(async () => {
      await Promise.all([
        api().getAccessToken(),
        api().getAccessToken(),
        api().getAccessToken(),
      ]);
    });

    expect(renewal).toHaveBeenCalledTimes(1);
  });
});

describe("signIn", () => {
  it("sends the browser to the authorize endpoint", async () => {
    renderProvider();

    await act(async () => {
      await api().signIn({ redirect: "/submissions" });
    });

    expect(assigned).toHaveLength(1);
    const url = new URL(assigned[0]);
    expect(url.origin + url.pathname).toBe(DISCOVERY.authorization_endpoint);
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  });
});

describe("signOut", () => {
  it("clears the local session before redirecting to end the remote one", async () => {
    const renewal = succeedingRenewal();
    renderProvider(renewal);
    await act(async () => {
      await api().restore();
    });
    expect(screen.getByTestId("status")).toHaveTextContent("signed-in");

    await act(async () => {
      await api().signOut();
    });

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
    });
    // The failure mode this guards: ending the remote session but leaving
    // the local one, so a "signed out" user is still signed in. Asking for
    // a token now must go back to the provider rather than hand back the
    // cached one - a second renewal call is what proves the cache was
    // dropped. (The stub's SSO session is still alive, so the renewal
    // itself succeeds; in a real logout Keycloak would answer
    // `login_required`, which is covered by the integration test in
    // backend/tests/test_keycloak_pkce_login.py.)
    await act(async () => {
      await api().getAccessToken();
    });
    expect(renewal).toHaveBeenCalledTimes(2);
  });

  it("names the session to end, so Keycloak does not re-prompt", async () => {
    renderProvider(succeedingRenewal());
    await act(async () => {
      await api().restore();
    });
    assigned = [];

    await act(async () => {
      await api().signOut();
    });

    const url = new URL(assigned[0]);
    expect(url.origin + url.pathname).toBe(DISCOVERY.end_session_endpoint);
    expect(url.searchParams.get("id_token_hint")).toBe("id-token");
  });

  it("does nothing but clear locally when there was no session", async () => {
    renderProvider();

    await act(async () => {
      await api().signOut();
    });

    expect(assigned).toHaveLength(0);
    expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
  });
});

describe("completeCallback", () => {
  it("returns the stored destination and becomes signed in", async () => {
    renderProvider();
    // Start a real flow so a genuine transaction exists to match against.
    await act(async () => {
      await api().signIn({ redirect: "/submissions" });
    });
    const state = new URL(assigned[0]).searchParams.get("state") ?? "";

    let destination: string | null = null;
    await act(async () => {
      destination = await api().completeCallback(
        new URLSearchParams({ code: "the-code", state }),
      );
    });

    expect(destination).toBe("/submissions");
    expect(screen.getByTestId("status")).toHaveTextContent("signed-in");
  });

  it("returns null, not a thrown error, when the state does not match", async () => {
    renderProvider();
    await act(async () => {
      await api().signIn();
    });

    let destination: string | null = "unset";
    await act(async () => {
      destination = await api().completeCallback(
        new URLSearchParams({ code: "the-code", state: "wrong" }),
      );
    });

    expect(destination).toBeNull();
    expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
  });

  it("defaults to the home page when no destination was stored", async () => {
    renderProvider();
    await act(async () => {
      await api().signIn();
    });
    const state = new URL(assigned[0]).searchParams.get("state") ?? "";

    let destination: string | null = null;
    await act(async () => {
      destination = await api().completeCallback(
        new URLSearchParams({ code: "the-code", state }),
      );
    });

    expect(destination).toBe("/");
  });
});
