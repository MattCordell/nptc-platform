import { refusalDetail } from "../api/conflicts.ts";
import { useConceptLookup } from "../api/queries.ts";
import { chosenConcept } from "./code-binding.ts";
import type { ChosenConcept } from "./code-binding.ts";

/**
 * Where a picked code stands: not picked, still being named by the
 * terminology server, unable to be named, or ready to bind.
 *
 * Only `ready` may be sent. The names come from `useConceptLookup`, never
 * from the editor (FR-26, FR-82).
 */
export type CodeSelection =
  | { status: "none" }
  | { status: "checking"; code: string }
  | { status: "unresolved"; code: string; message: string }
  | { status: "ready"; concept: ChosenConcept };

export function useCodeSelection(code: string | null): CodeSelection {
  const lookup = useConceptLookup(code ?? "");
  if (code === null) {
    return { status: "none" };
  }
  if (lookup.isPending) {
    return { status: "checking", code };
  }
  if (lookup.isError) {
    return {
      status: "unresolved",
      code,
      message:
        refusalDetail(lookup.error) ?? "This code could not be checked. Try again.",
    };
  }
  const concept = chosenConcept(lookup.data);
  if (concept === null) {
    return {
      status: "unresolved",
      code,
      message: `The terminology server did not return a name for ${code}. It cannot be bound until it does.`,
    };
  }
  return { status: "ready", concept };
}
