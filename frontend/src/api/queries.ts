import { useQuery } from "@tanstack/react-query";

import { unwrap } from "./unwrap.ts";
import { useApiClient } from "./use-api-client.ts";

/**
 * TanStack Query hooks over the generated client (issue #147).
 *
 * Infrastructure only: these are the representative wiring for the pattern
 * every future catalogue-data hook follows, not a page consuming them yet
 * (that lands with the catalogue UI issues). Query keys are the literal
 * OpenAPI path plus the params that vary the response, so two components
 * requesting the same resource share one cache entry.
 */

export interface EntriesListParams {
  limit?: number;
  after?: string | null;
}

export function useEntriesList(params: EntriesListParams = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["api", "/api/v1/catalogue/entries", params],
    queryFn: async ({ signal }) =>
      unwrap(
        await client.GET("/api/v1/catalogue/entries", {
          params: { query: params },
          signal,
        }),
      ),
  });
}

export function useEntryDetail(businessKey: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["api", "/api/v1/catalogue/entries/{business_key}", businessKey],
    queryFn: async ({ signal }) =>
      unwrap(
        await client.GET("/api/v1/catalogue/entries/{business_key}", {
          params: { path: { business_key: businessKey } },
          signal,
        }),
      ),
    // A blank business key can't resolve to a real entry - don't fire the
    // request only to fail with a 422.
    enabled: businessKey.length > 0,
  });
}
