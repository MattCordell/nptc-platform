import createClient from "openapi-fetch";

import type { AuthContextValue } from "../auth/session.ts";
import type { paths } from "./schema.ts";

/**
 * The one typed transport for the backend's public API (FR-06, issue #147).
 *
 * Built over `paths` from {@link ./schema.ts} - the openapi-typescript output
 * generated from `docs/api/openapi.json` - so a path, its params, and its
 * response shape are all checked at `pnpm typecheck` time. No hand-written
 * request/response interface should ever exist alongside this: if the
 * generated type is missing a field, that is a staleness bug to fix by
 * regenerating (`pnpm generate:api`), not a reason to hand-author one.
 *
 * `getAccessToken` is called on every request rather than read once, per
 * `AuthContextValue`'s own contract (see `src/auth/session.ts`): a renewal
 * may be pending, and callers must always await it instead of caching the
 * string.
 */
export function createApiClient(options: {
  baseUrl: string;
  getAccessToken: AuthContextValue["getAccessToken"];
}) {
  const client = createClient<paths>({ baseUrl: options.baseUrl });

  client.use({
    onRequest: async ({ request }) => {
      const token = await options.getAccessToken();
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }
      return request;
    },
  });

  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;
