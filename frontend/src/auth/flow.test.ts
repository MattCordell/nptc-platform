import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthConfig } from "./config.ts";
import { resetEndpointCache } from "./discovery.ts";
import {
  AuthFlowError,
  buildAuthorizeUrl,
  buildLogoutUrl,
  completeSignIn,
  InteractionRequiredError,
} from "./flow.ts";
import { clearTransactions } from "./transaction.ts";

/**
 * The browser half of the PKCE flow (issue #41, NFR-01).
 *
 * The checks Keycloak owns - that a mismatched `code_verifier` and a
 * replayed authorisation code are both refused - are proved against a real
 * Keycloak in `backend/tests/test_keycloak_pkce_login.py`. Asserting them
 * against a stub here would only prove the stub agreed with the assertion.
 * What *this* file owns is the half the browser is solely responsible for:
 * the `state` check and the single-use transaction.
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Records every token-endpoint request so its body can be asserted. */
let tokenRequests: { url: string; body: URLSearchParams }[] = [];

beforeEach(() => {
  resetEndpointCache();
  clearTransactions();
  tokenRequests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes(".well-known")) {
        return Promise.resolve(jsonResponse(DISCOVERY));
      }
      if (url === DISCOVERY.token_endpoint) {
        tokenRequests.push({
          url,
          body: new URLSearchParams(String(init?.body ?? "")),
        });
        return Promise.resolve(
          jsonResponse({
            access_token: "access-token-value",
            id_token: "id-token-value",
            token_type: "Bearer",
            expires_in: 300,
          }),
        );
      }
      throw new Error(`unexpected fetch to ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Starts a flow and returns the `state` it committed to. */
async function beginAndGetState(redirect?: string): Promise<string> {
  const url = new URL(await buildAuthorizeUrl(CONFIG, { redirect }));
  const state = url.searchParams.get("state");
  if (!state) {
    throw new Error("authorize URL carried no state");
  }
  return state;
}

describe("buildAuthorizeUrl", () => {
  it("requests an S256 PKCE challenge and never sends a secret", async () => {
    const url = new URL(await buildAuthorizeUrl(CONFIG));

    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(url.searchParams.get("client_id")).toBe("nptc-frontend");
    expect(url.searchParams.get("redirect_uri")).toBe(CONFIG.redirectUri);
    // NFR-01: a public client has no secret, and none may appear anywhere.
    expect(url.toString()).not.toMatch(/secret/i);
    // The verifier itself must never leave the browser at this stage - only
    // its hash does. Sending it here would defeat PKCE entirely.
    expect(url.searchParams.get("code_verifier")).toBeNull();
  });

  it("gives every attempt a fresh state and challenge", async () => {
    const first = new URL(await buildAuthorizeUrl(CONFIG));
    const second = new URL(await buildAuthorizeUrl(CONFIG));

    expect(first.searchParams.get("state")).not.toBe(second.searchParams.get("state"));
    expect(first.searchParams.get("code_challenge")).not.toBe(
      second.searchParams.get("code_challenge"),
    );
  });

  it("carries prompt=none and acr_values only when asked", async () => {
    const plain = new URL(await buildAuthorizeUrl(CONFIG));
    expect(plain.searchParams.get("prompt")).toBeNull();
    expect(plain.searchParams.get("acr_values")).toBeNull();

    const stepUp = new URL(
      await buildAuthorizeUrl(CONFIG, { prompt: "none", acrValues: "2" }),
    );
    expect(stepUp.searchParams.get("prompt")).toBe("none");
    // NFR-06: the level the realm's LoA-2 subflow requires OTP for.
    expect(stepUp.searchParams.get("acr_values")).toBe("2");
  });
});

describe("completeSignIn - state validation", () => {
  it("exchanges the code when the state matches", async () => {
    const state = await beginAndGetState("/submissions");

    const result = await completeSignIn(
      CONFIG,
      new URLSearchParams({ code: "auth-code", state }),
    );

    expect(result.tokens.accessToken).toBe("access-token-value");
    expect(result.redirect).toBe("/submissions");
    expect(tokenRequests).toHaveLength(1);
  });

  it("refuses a callback whose state does not match", async () => {
    await beginAndGetState();

    await expect(
      completeSignIn(CONFIG, new URLSearchParams({ code: "auth-code", state: "not-it" })),
    ).rejects.toBeInstanceOf(AuthFlowError);
    // Refused before the code went anywhere - the point of checking first.
    expect(tokenRequests).toHaveLength(0);
  });

  it("refuses a callback carrying no state at all", async () => {
    await beginAndGetState();

    await expect(
      completeSignIn(CONFIG, new URLSearchParams({ code: "auth-code" })),
    ).rejects.toBeInstanceOf(AuthFlowError);
    expect(tokenRequests).toHaveLength(0);
  });

  it("refuses a replayed callback, because the transaction is single use", async () => {
    const state = await beginAndGetState();
    const search = new URLSearchParams({ code: "auth-code", state });

    await completeSignIn(CONFIG, search);

    // The identical URL a second time - a refresh, a bookmark, or a link an
    // attacker fed to the user.
    await expect(completeSignIn(CONFIG, search)).rejects.toBeInstanceOf(AuthFlowError);
    expect(tokenRequests).toHaveLength(1);
  });

  it("refuses a callback when no sign-in was ever started in this tab", async () => {
    await expect(
      completeSignIn(
        CONFIG,
        new URLSearchParams({ code: "auth-code", state: "anything" }),
      ),
    ).rejects.toBeInstanceOf(AuthFlowError);
    expect(tokenRequests).toHaveLength(0);
  });

  it("refuses a callback with no authorisation code", async () => {
    const state = await beginAndGetState();

    await expect(
      completeSignIn(CONFIG, new URLSearchParams({ state })),
    ).rejects.toBeInstanceOf(AuthFlowError);
  });
});

describe("completeSignIn - provider errors", () => {
  it("reports login_required as interaction-required, not a failure", async () => {
    await beginAndGetState();

    await expect(
      completeSignIn(CONFIG, new URLSearchParams({ error: "login_required" })),
    ).rejects.toBeInstanceOf(InteractionRequiredError);
  });

  it("treats any other provider error as a flow failure", async () => {
    await beginAndGetState();

    const rejection = completeSignIn(
      CONFIG,
      new URLSearchParams({ error: "access_denied" }),
    );
    await expect(rejection).rejects.toBeInstanceOf(AuthFlowError);
    await expect(rejection).rejects.not.toBeInstanceOf(InteractionRequiredError);
  });
});

describe("the token exchange", () => {
  it("sends the verifier and no client secret", async () => {
    const state = await beginAndGetState();
    await completeSignIn(CONFIG, new URLSearchParams({ code: "auth-code", state }));

    const [request] = tokenRequests;
    expect(request.body.get("grant_type")).toBe("authorization_code");
    expect(request.body.get("code_verifier")).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(request.body.get("redirect_uri")).toBe(CONFIG.redirectUri);
    // NFR-01, asserted on the wire rather than by reading the source.
    expect(request.body.get("client_secret")).toBeNull();
    expect(String(request.body)).not.toMatch(/secret/i);
  });

  it("turns expires_in into an absolute expiry", async () => {
    const state = await beginAndGetState();
    const before = Date.now();

    const { tokens } = await completeSignIn(
      CONFIG,
      new URLSearchParams({ code: "auth-code", state }),
    );

    expect(tokens.expiresAt).toBeGreaterThanOrEqual(before + 300_000);
  });
});

describe("buildLogoutUrl", () => {
  it("names the session to end and where to come back to", async () => {
    const url = new URL(await buildLogoutUrl(CONFIG, "id-token-value"));

    // Without id_token_hint Keycloak asks the user to confirm a logout they
    // have already asked for.
    expect(url.searchParams.get("id_token_hint")).toBe("id-token-value");
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(
      CONFIG.postLogoutRedirectUri,
    );
  });
});
