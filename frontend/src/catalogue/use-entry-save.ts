import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { adminEntryDetailKey } from "../api/queries.ts";
import { unwrap } from "../api/unwrap.ts";
import { useApiClient } from "../api/use-api-client.ts";
import { runSave } from "./run-save.ts";
import type { FieldChange, SaveRun, SendResult } from "./run-save.ts";

export interface EntrySaveRequest {
  changes: FieldChange[];
  note: string;
  rowVersion: number;
}

/**
 * Sends the edit form's changes through `runSave`.
 *
 * It calls the client directly and not the per-field mutation hooks, because
 * those invalidate the entry after every call. A refetch in the middle of a
 * run would reset the form under an editor whose later fields are still
 * waiting. The entry is refetched once, after the run, whatever its outcome:
 * a stopped run needs the fresh `row_version` as much as a finished one.
 *
 * The error that stopped a run is rethrown from the mutation function and
 * `save` still resolves with the run. The rethrow is what lets the query
 * client's mutation cache see a step-up challenge or a terms refusal
 * (ADR-0036, NFR-45), which it recognises on any failed mutation.
 */
export function useEntrySave(businessKey: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const lastRun = useRef<SaveRun | null>(null);

  const mutation = useMutation<SaveRun, unknown, EntrySaveRequest>({
    mutationFn: async ({ changes, note, rowVersion }) => {
      lastRun.current = null;
      const run = await runSave(
        changes,
        async (change, version): Promise<SendResult> => {
          if (change.kind === "preferred_term") {
            const result = unwrap(
              await client.POST(
                "/api/v1/catalogue/entries/{business_key}/designations/amendment",
                {
                  params: { path: { business_key: businessKey } },
                  body: {
                    term: change.currentTerm,
                    new_term: change.newTerm,
                    target: "preferred_term",
                    expected_row_version: version,
                    reason: note,
                  },
                },
              ),
            );
            return {
              rowVersion: result.row_version,
              warnings: result.warnings,
              length: result.designation.length,
              savedTerm: result.designation.term,
            };
          }
          const result = unwrap(
            await client.PUT(
              "/api/v1/catalogue/entries/{business_key}/properties/{key}",
              {
                params: { path: { business_key: businessKey, key: change.key } },
                body: {
                  values: change.values,
                  reason: note,
                  expected_row_version: version,
                },
              },
            ),
          );
          return { rowVersion: result.row_version };
        },
        rowVersion,
      );
      lastRun.current = run;
      const failed = run.outcomes.find((outcome) => outcome.status === "failed");
      if (failed?.status === "failed") {
        throw failed.error;
      }
      return run;
    },
    onSettled: () => {
      // Not returned: the mutation would wait for the refetch, and the summary
      // should appear as soon as the writes are done.
      void queryClient.invalidateQueries({ queryKey: adminEntryDetailKey(businessKey) });
    },
  });

  async function save(request: EntrySaveRequest): Promise<SaveRun> {
    try {
      return await mutation.mutateAsync(request);
    } catch (error) {
      if (lastRun.current !== null) {
        return lastRun.current;
      }
      throw error;
    }
  }

  return { save, isPending: mutation.isPending };
}
