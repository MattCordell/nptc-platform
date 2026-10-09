import type { components } from "../api/schema.ts";

/**
 * What a code binding write needs from the terminology server's answer, and
 * what has to be true before the editor may send it (FR-06, FR-26, FR-82).
 *
 * The editor never types the FSN or the AU preferred term. Both come from
 * `GET /terminology/concepts/{code}`, and a concept the server returned no
 * FSN for cannot be bound.
 */

type CodeBindingEditionHint = components["schemas"]["CodeBindingEditionHint"];
type ConceptLookup = components["schemas"]["ConceptLookup"];

/** A concept the terminology server named, ready to bind. `fsn` is exactly as served. */
export interface ChosenConcept {
  code: string;
  fsn: string;
  auPreferredTerm: string | null;
  edition: CodeBindingEditionHint;
  active: boolean | null;
}

/**
 * `ConceptLookup.edition` is a bare string, but a bind request takes the closed
 * `CodeBindingEditionHint` enum. An edition this screen does not recognise
 * falls back to `unknown` instead of failing the save.
 */
export function toEditionHint(edition: string): CodeBindingEditionHint {
  return edition === "au" || edition === "int" ? edition : "unknown";
}

/** The concept to bind, or `null` when the server returned no FSN for it. */
export function chosenConcept(lookup: ConceptLookup): ChosenConcept | null {
  if (lookup.fsn === null) {
    return null;
  }
  return {
    code: lookup.code,
    fsn: lookup.fsn,
    auPreferredTerm: lookup.au_preferred_term,
    edition: toEditionHint(lookup.edition),
    active: lookup.active,
  };
}
