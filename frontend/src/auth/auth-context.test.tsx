import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./auth-context.tsx";
import type { AuthConfig } from "./config.ts";
import { resetEndpointCache } from "./discovery.ts";
import { InteractionRequiredError } from "./flow.ts";
import { useAuth, type AuthContextValue } from "./session.ts";
import type { SilentAuthorize } from "./silent-renew.ts";
import { clearTransactions } from "./transaction.ts";

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

/**
 * The realistic default: no live SSO session, so the cold-load probe
 * answers `login_required` and the provider settles to signed-out. A bare
 * `vi.fn()` would resolve `undefined`, which is a *fault*, not "signed
 * out", and would settle every test into `unavailable`.
 */
function noSession() {
  return vi.fn(() => Promise.reject(new InteractionRequiredError("login_required")));
}

/** A probe that never answers, so `"restoring"` can be observed. */
function pendingRenewal() {
  return vi.fn(() => new Promise<URLSearchParams>(() => {}));
}

/**
 * A probe that stays pending until the test releases it, so a race against
 * `completeCallback` can be driven deterministically rather than relying on
 * incidental timing (issue #216).
 */
function releasableRenewal() {
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<URLSearchParams>((_, rej) => {
    reject = rej;
  });
  return {
    silentAuthorize: vi.fn(() => promise) as SilentAuthorize,
    refuse: () => reject(new InteractionRequiredError("login_required")),
  };
}

function renderProvider(silentAuthorize: SilentAuthorize = noSession()) {
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
  clearTransactions();
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
  it("starts in 'restoring', not 'signed-out'", () => {
    renderProvider(pendingRenewal());

    // The distinction the whole cold-load path rests on: tokens live in
    // memory, so a fresh page has none even with a live SSO session.
    // Reporting "signed-out" here would make `RequireAuth` redirect and
    // `/sign-in` start an interactive round trip before the silent probe
    // had answered - taking a signed-in user out of the SPA.
    expect(screen.getByTestId("status")).toHaveTextContent("restoring");
  });

  it("settles out of 'restoring' once the probe answers", async () => {
    const renewal = vi.fn(() =>
      Promise.reject(new InteractionRequiredError("login_required")),
    );
    renderProvider(renewal);

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
    });
  });

  it("settles out of 'restoring' even if the probe throws outright", async () => {
    // A status nothing can move out of would hang the whole app, so the
    // probe settles in a `finally`.
    const renewal = vi.fn(() => Promise.reject(new Error("network down")));
    renderProvider(renewal);

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("unavailable");
    });
  });

  it("does not probe on the callback route", async () => {
    // The code exchange about to run there is what establishes the
    // session; a concurrent renewal would race it for the same callback.
    window.history.pushState({}, "", "/auth/callback?code=x&state=y");
    const renewal = pendingRenewal();
    try {
      renderProvider(renewal);
      await waitFor(() => {
        expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
      });
      expect(renewal).not.toHaveBeenCalled();
    } finally {
      window.history.pushState({}, "", "/");
    }
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

  it("stops reporting unavailable once something succeeds", async () => {
    // It used to be sticky for the life of the tab, so one transient blip
    // permanently degraded the shell - including for a user who then
    // signed in perfectly well.
    let fail = true;
    const renewal = vi.fn((url: string) => {
      if (fail) {
        return Promise.reject(new Error("network down"));
      }
      const state = new URL(url).searchParams.get("state") ?? "";
      return Promise.resolve(new URLSearchParams({ code: "silent-code", state }));
    });
    renderProvider(renewal);

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("unavailable");
    });

    fail = false;
    await act(async () => {
      await api().getAccessToken();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("signed-in");
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

describe("cold-load probe racing a concurrent sign-in (issue #216)", () => {
  it("does not clear a session completeCallback established while the probe is still in flight", async () => {
    const { silentAuthorize, refuse } = releasableRenewal();
    // The cold-load probe starts on mount and is now in flight, pending on
    // `silentAuthorize` until `refuse()` below.
    renderProvider(silentAuthorize);

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

    // The probe's late answer: an ordinary "no SSO session" refusal, the
    // path that used to clear the session `completeCallback` just
    // established. A macrotask boundary, not a fixed number of
    // microtask ticks, so the refusal has fully travelled the `await
    // silentAuthorize(...)` resumption, the catch, the renewal settling
    // and `restore()`'s own `finally` before the assertion runs - a
    // microtask-only flush could pass merely because it ran before any
    // of that had happened.
    await act(async () => {
      refuse();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByTestId("status")).toHaveTextContent("signed-in");
  });

  it("still clears an established session when its own expiry renewal is refused", async () => {
    // The negative case: guarding the clear must not turn into never
    // clearing. A signed-in session whose own renewal is refused - nothing
    // else racing it - must still be cleared, exactly as before this fix.
    //
    // The token endpoint hands back an already-expired token (`expires_in:
    // -1`), so the next `getAccessToken` call triggers a real renewal
    // rather than reusing the cached token - no fake timers needed.
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
              expires_in: -1,
            }),
            { headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );
    let succeed = true;
    const renewal = vi.fn((url: string) => {
      if (succeed) {
        const state = new URL(url).searchParams.get("state") ?? "";
        return Promise.resolve(new URLSearchParams({ code: "silent-code", state }));
      }
      return Promise.reject(new InteractionRequiredError("login_required"));
    });
    renderProvider(renewal);
    // The mount-time cold-load probe is what signs this in, not a call to
    // `restore()` - it already ran and stored the session, so a second
    // `restore()` here would be a no-op (`tokensRef.current` is already
    // set). `waitFor` just waits out that probe's own async chain.
    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("signed-in");
    });

    succeed = false;
    await act(async () => {
      await api().getAccessToken();
    });

    expect(screen.getByTestId("status")).toHaveTextContent("signed-out");
  });

  it("falls back to the ref so a caller mid-race still gets the session completeCallback established", async () => {
    const { silentAuthorize, refuse } = releasableRenewal();
    renderProvider(silentAuthorize);

    // Started while tokens are still null, so it joins the mount probe's
    // in-flight renewal (the de-duped `renewal.current`) rather than
    // returning a cached token - this is the caller the fallback exists
    // for. Deliberately not awaited yet: it must stay pending across
    // `completeCallback` below, or this test would pass the same way it
    // would if `getAccessToken` were called after the session already
    // existed, which proves nothing about the fallback.
    const pending = api().getAccessToken();

    await act(async () => {
      await api().signIn({ redirect: "/submissions" });
    });
    const state = new URL(assigned[0]).searchParams.get("state") ?? "";

    await act(async () => {
      await api().completeCallback(new URLSearchParams({ code: "the-code", state }));
    });

    // A caller that asks for a token while the probe is still refusing must
    // not be told "signed out" - `renew()`'s own resolved value is a stale
    // `null`, but the ref reflects the session that actually won the race.
    let token: string | null = "unset";
    await act(async () => {
      refuse();
      token = await pending;
    });

    expect(token).toBe("access-token");
  });
});
