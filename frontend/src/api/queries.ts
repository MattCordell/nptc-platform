import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { asVersionConflict } from "./conflicts.ts";
import type { components } from "./schema.ts";
import { unwrap } from "./unwrap.ts";
import { useApiClient } from "./use-api-client.ts";

/**
 * TanStack Query hooks over the generated client (issue #147).
 *
 * Query keys are the literal OpenAPI path plus the params that vary the
 * response, so two components requesting the same resource share one cache
 * entry.
 *
 * The write hooks below are the first mutations in this app (issue #149), so
 * their shape is the precedent #150 and #151 inherit. Three rules:
 *
 * - **A mutation never patches the cache by hand.** Every one invalidates the
 *   admin-detail key and lets the screen re-read. A designation write can move
 *   more than the row it names - `add_synonyms` inserts in comparison-key
 *   order, and amending the entry's own preferred term changes its `length`
 *   (FR-85) and `row_version` - so reconstructing the new state client-side
 *   would be a second, weaker implementation of what the read route already
 *   returns correctly.
 * - **The hook takes the business key, the mutation takes the body.** The key
 *   is what scopes the invalidation, and it does not change between calls.
 * - **Failures are thrown, not returned.** `unwrap` gates on `response.ok` and
 *   throws `ApiError`, so a refusal reaches the caller's `onError`/`error`
 *   with its parsed body intact for `./conflicts.ts` to narrow. The one
 *   failure that also invalidates is FR-38's version conflict - see
 *   `useAmendDesignation`.
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

/**
 * The query key every write below invalidates. Exported so a test can assert
 * the invalidation against the same value the hooks use, rather than a
 * hand-retyped copy that could drift from it.
 */
export function adminEntryDetailKey(businessKey: string) {
  return ["api", "/api/v1/catalogue/admin/entries/{business_key}", businessKey] as const;
}

/**
 * One entry, **any status**, for an editing screen (issue #228).
 *
 * Distinct from `useEntryDetail`: the public route serves only `active`
 * entries and 404s a `draft` one identically to a key that was never minted,
 * so an edit screen cannot load its own subject through it. This route serves
 * the same `EntryDetail` shape behind `catalogue.edit_published`.
 */
export function useAdminEntryDetail(businessKey: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: adminEntryDetailKey(businessKey),
    queryFn: async ({ signal }) =>
      unwrap(
        await client.GET("/api/v1/catalogue/admin/entries/{business_key}", {
          params: { path: { business_key: businessKey } },
          signal,
        }),
      ),
    enabled: businessKey.length > 0,
  });
}

type AddDesignationsBody = components["schemas"]["AddDesignationsRequest"];
type AmendDesignationBody = components["schemas"]["AmendDesignationRequest"];
type RetireDesignationBody = components["schemas"]["RetireDesignationRequest"];
type AcknowledgeCollisionBody = components["schemas"]["AcknowledgeCollisionRequest"];
type BindCodeBody = components["schemas"]["BindCodeRequest"];
type RetireBindingBody = components["schemas"]["RetireBindingRequest"];
type ReplaceBindingBody = components["schemas"]["ReplaceBindingRequest"];

/**
 * Add one or more terms to an entry (FR-04).
 *
 * `terms` is a batch because the case this exists for is a pasted,
 * delimiter-corrupted synonym cell - split by `catalogue/split-synonyms.ts`
 * before it gets here (ADR-0030).
 */
export function useAddDesignations(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AddDesignationsBody) =>
      unwrap(
        await client.POST("/api/v1/catalogue/entries/{business_key}/designations", {
          params: { path: { business_key: businessKey } },
          body,
        }),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}

/**
 * Edit a term in place - either a `designation` row or the entry's own en-AU
 * preferred term (issue #227).
 *
 * One route, two storage homes (ADR-0022): the caller sends `use: "preferred"`
 * with the default language to address the entry itself, and
 * `expected_row_version` to satisfy FR-38's optimistic lock. The screen sends
 * the version on *every* amendment - the route honours it on both branches and
 * requires it on one, so sending it unconditionally is one code path and no
 * silently unguarded save.
 */
export function useAmendDesignation(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AmendDesignationBody) =>
      unwrap(
        await client.POST(
          "/api/v1/catalogue/entries/{business_key}/designations/amendment",
          {
            params: { path: { business_key: businessKey } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
    // A version conflict is the one failure that also has to refetch. The
    // refusal tells the editor the entry moved under them; without this the
    // cached `row_version` stays stale, so every retry from the open dialog
    // fails identically and the only way out is a browser reload (review
    // finding 3). Refetching here is what makes "save again" true advice.
    onError: (error: unknown) => {
      if (asVersionConflict(error) !== null) {
        void queryClient.invalidateQueries({
          queryKey: adminEntryDetailKey(businessKey),
        });
      }
    },
  });
}

/**
 * Retire a designation - a status transition, never a delete.
 *
 * Only ever a `designation` row: `catalogue_entry.preferred_term` is `NOT NULL`
 * and there is no route to retire it (ADR-0022), which is why the screen does
 * not offer the action on that row.
 */
export function useRetireDesignation(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: RetireDesignationBody) =>
      unwrap(
        await client.POST(
          "/api/v1/catalogue/entries/{business_key}/designations/retirement",
          {
            params: { path: { business_key: businessKey } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}

/**
 * Acknowledge a warning-severity collision, so it stops recurring on every
 * save (FR-05).
 *
 * Gated on `validation.acknowledge`, not `catalogue.edit_published` - a
 * Reviewer holds it and an Administrator holds it, and unlike the other three
 * it is not MFA-gated. So a caller who can edit is not guaranteed to be able
 * to acknowledge, and this mutation's 403 is a different sentence from theirs.
 */
export function useAcknowledgeCollision(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AcknowledgeCollisionBody) =>
      unwrap(
        await client.POST(
          "/api/v1/catalogue/entries/{business_key}/designations/acknowledgement",
          {
            params: { path: { business_key: businessKey } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}

/**
 * Resolve one SNOMED CT code's served FSN, AU preferred term and active
 * status (FR-26, issue #240) - the one thing the code binding form (#150)
 * needs so an editor never types a label, only a code.
 *
 * `enabled` gates on a non-empty code rather than on looking like a well-formed
 * SCTID: the route's own pre-flight already turns a malformed candidate into a
 * cheap 422 with no upstream call, and duplicating that shape check here would
 * be a second, weaker copy of `SCTID`'s own rule (ADR-0030's third condition -
 * the server stays the authority). The caller debounces keystrokes before
 * this ever fires; `retry` stays the app-wide `false` (`query-client.ts`) so a
 * 404/503/502 shows as-is rather than retrying blind on top of the retries the
 * route itself already did against Ontoserver.
 */
export function useConceptLookup(code: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["api", "/api/v1/terminology/concepts/{code}", code],
    queryFn: async ({ signal }) =>
      unwrap(
        await client.GET("/api/v1/terminology/concepts/{code}", {
          params: { path: { code } },
          signal,
        }),
      ),
    enabled: code.length > 0,
    // Repeats within one form session - typing, then backspacing to the same
    // code, or opening Replace after Bind already resolved it - should not
    // re-hit Ontoserver. Not a cache that should outlive the session: this is
    // TanStack Query's own in-memory store, gone on reload, never persisted.
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Bind a SNOMED CT code to an entry (FR-08, FR-26, FR-82; issue #150).
 *
 * `fsn`/`au_preferred_term` in the body come from `useConceptLookup`'s
 * answer, never typed - this hook does not re-check that, the same way
 * `useAmendDesignation` does not re-check `expected_row_version` was ever
 * fetched. Shown only while the entry has no active binding (FR-08 permits at
 * most one); the panel enforces that, not this hook.
 */
export function useBindCode(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: BindCodeBody) =>
      unwrap(
        await client.POST("/api/v1/catalogue/entries/{business_key}/bindings", {
          params: { path: { business_key: businessKey } },
          body,
        }),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}

/**
 * Retire an entry's active code binding (FR-08) - a status transition, never
 * a delete. Addressed by the binding's currently-active `code` in the path,
 * not an id: `Binding` deliberately carries none.
 */
export function useRetireBinding(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ code, body }: { code: string; body: RetireBindingBody }) =>
      unwrap(
        await client.POST(
          "/api/v1/catalogue/entries/{business_key}/bindings/{code}/retirement",
          {
            params: { path: { business_key: businessKey, code } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}

/**
 * Retire an entry's active code binding and bind its successor in one
 * request (FR-08) - the only route that populates a retired binding's
 * `replaced_by_code`, which is why Retire and Replace are two separate
 * actions rather than one dialog with a conditional branch.
 */
export function useReplaceBinding(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ code, body }: { code: string; body: ReplaceBindingBody }) =>
      unwrap(
        await client.POST(
          "/api/v1/catalogue/entries/{business_key}/bindings/{code}/replacement",
          {
            params: { path: { business_key: businessKey, code } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) }),
  });
}
