import { useMemo } from "react";

import { useAuth } from "../auth/session.ts";
import { createApiClient, type ApiClient } from "./client.ts";

/**
 * The api client for the current render, memoised on the auth context
 * identity so query hooks built on top of it don't refetch just because a
 * parent re-rendered (issue #147).
 *
 * `VITE_API_BASE_URL` defaults to same-origin - `window.location.origin`,
 * not `""` - since Caddy fronts both the frontend and the backend in every
 * deployed environment (ADR-0001); it carries no credential, so it needs no
 * `assert-no-secret-in-bundle.mjs` allowance. An explicit origin rather than
 * an empty string matters beyond style: `Request`'s URL parsing (used by
 * `openapi-fetch`) requires an absolute URL - unlike a browser's `fetch`,
 * it does not resolve a relative one against the document's location.
 */
export function useApiClient(): ApiClient {
  const auth = useAuth();
  return useMemo(
    () =>
      createApiClient({
        baseUrl: import.meta.env.VITE_API_BASE_URL || window.location.origin,
        getAccessToken: auth.getAccessToken,
      }),
    [auth.getAccessToken],
  );
}
