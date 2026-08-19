import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadEndpoints, resetEndpointCache } from "./discovery.ts";

/**
 * OIDC discovery (issue #41).
 *
 * The issuer check is the one with teeth: a document that names a different
 * issuer would send the login redirect to another authorisation server, and
 * the user would be shown a convincing login form belonging to someone
 * else. `nptc.auth.discovery` makes the same refusal server-side.
 */

const ISSUER = "https://idp.test/realms/nptc";

const DOCUMENT = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
  token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
  end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
};

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  resetEndpointCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("loadEndpoints", () => {
  it("reads the three endpoints the flow needs", async () => {
    stubFetch(DOCUMENT);

    await expect(loadEndpoints(ISSUER)).resolves.toEqual({
      authorizationEndpoint: DOCUMENT.authorization_endpoint,
      tokenEndpoint: DOCUMENT.token_endpoint,
      endSessionEndpoint: DOCUMENT.end_session_endpoint,
    });
  });

  it("fetches once and caches", async () => {
    const fetchMock = stubFetch(DOCUMENT);

    await loadEndpoints(ISSUER);
    await loadEndpoints(ISSUER);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses a document naming a different issuer", async () => {
    stubFetch({ ...DOCUMENT, issuer: "https://evil.test/realms/nptc" });

    await expect(loadEndpoints(ISSUER)).rejects.toThrow(/issuer/i);
  });

  it("refuses a document missing an endpoint", async () => {
    const incomplete: Record<string, unknown> = { ...DOCUMENT };
    delete incomplete.token_endpoint;
    stubFetch(incomplete);

    await expect(loadEndpoints(ISSUER)).rejects.toThrow(/token_endpoint/);
  });

  it("refuses a non-2xx response", async () => {
    stubFetch({}, 503);

    await expect(loadEndpoints(ISSUER)).rejects.toThrow(/503/);
  });

  it("does not cache a failure, so a later attempt can still succeed", async () => {
    // The realistic case: the SPA loads while Keycloak is still starting.
    // Caching that failure would break sign-in for the life of the tab.
    stubFetch({}, 503);
    await expect(loadEndpoints(ISSUER)).rejects.toThrow();

    stubFetch(DOCUMENT);
    await expect(loadEndpoints(ISSUER)).resolves.toHaveProperty("tokenEndpoint");
  });
});
