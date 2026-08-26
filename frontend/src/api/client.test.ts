import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./client.ts";

/**
 * The typed transport (issue #147). Behaviour under test is the one thing
 * this file adds over plain `openapi-fetch`: attaching a bearer token from
 * `AuthContextValue.getAccessToken`, awaited fresh on every request.
 */

function stubFetch(status = 200, body: unknown = {}) {
  return vi.fn<(request: Request) => Promise<Response>>((request) => {
    void request;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
}

describe("createApiClient", () => {
  it("attaches a bearer token from getAccessToken to every request", async () => {
    const fetchMock = stubFetch();
    const getAccessToken = vi.fn().mockResolvedValue("the-token");
    const client = createApiClient({
      baseUrl: "https://api.test",
      getAccessToken,
    });

    await client.GET("/api/v1/auth/me", { fetch: fetchMock });

    expect(getAccessToken).toHaveBeenCalledTimes(1);
    const requestArg = fetchMock.mock.calls[0]?.[0] as Request;
    expect(requestArg.headers.get("Authorization")).toBe("Bearer the-token");
  });

  it("sends no Authorization header when there is no token", async () => {
    const fetchMock = stubFetch();
    const client = createApiClient({
      baseUrl: "https://api.test",
      getAccessToken: () => Promise.resolve(null),
    });

    await client.GET("/api/v1/auth/me", { fetch: fetchMock });

    const requestArg = fetchMock.mock.calls[0]?.[0] as Request;
    expect(requestArg.headers.has("Authorization")).toBe(false);
  });

  it("re-checks getAccessToken on every request rather than caching it", async () => {
    const fetchMock = stubFetch();
    const getAccessToken = vi
      .fn()
      .mockResolvedValueOnce("first-token")
      .mockResolvedValueOnce("second-token");
    const client = createApiClient({
      baseUrl: "https://api.test",
      getAccessToken,
    });

    await client.GET("/api/v1/auth/me", { fetch: fetchMock });
    await client.GET("/api/v1/auth/me", { fetch: fetchMock });

    expect(getAccessToken).toHaveBeenCalledTimes(2);
    const first = fetchMock.mock.calls[0]?.[0] as Request;
    const second = fetchMock.mock.calls[1]?.[0] as Request;
    expect(first.headers.get("Authorization")).toBe("Bearer first-token");
    expect(second.headers.get("Authorization")).toBe("Bearer second-token");
  });
});
