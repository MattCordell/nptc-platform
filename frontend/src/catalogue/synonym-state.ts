import type { components } from "../api/schema.ts";
import { MAX_TERMS_PER_BATCH } from "./limits.ts";
import { normaliseForComparison } from "./python-text.ts";
import { splitSynonyms } from "./split-synonyms.ts";
import type {
  SynonymAmendChange,
  SynonymRetireChange,
  SynonymsAddChange,
} from "./run-save.ts";

/**
 * The edit form's RCPA Synonyms, as the editor has left them (FR-04, FR-36).
 *
 * Every `designation` row is a synonym (ADR-0022). One row per active
 * synonym, so a retired one is not here. `original` is the stored term, which
 * the write routes address a synonym by. A row the editor removed stays in
 * the list, marked, until Save retires it, so a mistaken Remove can be undone.
 *
 * Pure, so the three ways a row can change (amended, retired, added) and the
 * merge after the entry is refetched are testable with no React.
 */

type Designation = components["schemas"]["Designation"];

export interface SynonymRow {
  id: string;
  original: string;
  term: string;
  removed: boolean;
}

export const ADD_CHANGE_ID = "synonyms:add";
export const ADD_SYNONYMS_FIELD_ID = "entry-add-synonyms";

export function synonymFieldId(rowId: string): string {
  return `entry-synonym-${rowId}`;
}

export function amendChangeId(original: string): string {
  return `synonym:amend:${original}`;
}

export function retireChangeId(original: string): string {
  return `synonym:retire:${original}`;
}

export function activeSynonyms(designations: Designation[]): string[] {
  return designations
    .filter((designation) => designation.status === "active")
    .map((designation) => designation.term);
}

function touched(row: SynonymRow): boolean {
  return row.removed || row.term !== row.original;
}

/**
 * The rows to show once the entry holds `freshTerms`. A row the editor changed
 * keeps their edit while its stored term is still there. An untouched row takes
 * what the entry now holds, so the editor never later changes a value they were
 * not shown, and overwrites whoever last changed it (FR-38).
 */
export function mergeSynonymRows(
  rows: SynonymRow[],
  freshTerms: string[],
  newId: () => string,
): SynonymRow[] {
  const keptByOriginal = new Map<string, SynonymRow>();
  for (const row of rows) {
    if (freshTerms.includes(row.original) && !keptByOriginal.has(row.original)) {
      keptByOriginal.set(row.original, row);
    }
  }
  return freshTerms.map((term) => {
    const existing = keptByOriginal.get(term);
    if (existing !== undefined) {
      return existing;
    }
    return { id: newId(), original: term, term, removed: false };
  });
}

export function initialSynonymRows(terms: string[], newId: () => string): SynonymRow[] {
  return mergeSynonymRows([], terms, newId);
}

export function addedTerms(addText: string): string[] {
  return splitSynonyms(addText);
}

/** A row whose text is not blank once cleaned, or which is being removed. */
export function blankRows(rows: SynonymRow[]): SynonymRow[] {
  return rows.filter((row) => !row.removed && normaliseForComparison(row.term) === "");
}

export function addTextError(addText: string): string | null {
  const count = addedTerms(addText).length;
  if (count > MAX_TERMS_PER_BATCH) {
    return (
      `This adds ${count} terms, and at most ${MAX_TERMS_PER_BATCH} can be added at ` +
      "once. Split the paste into smaller batches."
    );
  }
  return null;
}

export interface SynonymChanges {
  amendments: { change: SynonymAmendChange; rowId: string }[];
  retirements: { change: SynonymRetireChange; rowId: string }[];
  addition: SynonymsAddChange | null;
}

/**
 * What a Save would send for the synonyms. An edit that cleans to the stored
 * term is not a change, and a blank row is not sent: `blankRows` names it as
 * an error first.
 */
export function synonymChanges(rows: SynonymRow[], addText: string): SynonymChanges {
  const amendments = rows
    .filter(
      (row) =>
        !row.removed &&
        normaliseForComparison(row.term) !== "" &&
        normaliseForComparison(row.term) !== normaliseForComparison(row.original),
    )
    .map((row) => ({
      rowId: row.id,
      change: {
        kind: "synonym_amend" as const,
        id: amendChangeId(row.original),
        label: `RCPA Synonym “${row.original}”`,
        currentTerm: row.original,
        newTerm: row.term,
      },
    }));
  const retirements = rows
    .filter((row) => row.removed)
    .map((row) => ({
      rowId: row.id,
      change: {
        kind: "synonym_retire" as const,
        id: retireChangeId(row.original),
        label: `Removing RCPA Synonym “${row.original}”`,
        term: row.original,
      },
    }));
  const terms = addedTerms(addText);
  return {
    amendments,
    retirements,
    addition:
      terms.length === 0
        ? null
        : {
            kind: "synonyms_add",
            id: ADD_CHANGE_ID,
            label:
              terms.length === 1
                ? "New RCPA Synonym"
                : `${terms.length} new RCPA Synonyms`,
            terms,
          },
  };
}

export function hasSynonymChanges(rows: SynonymRow[], addText: string): boolean {
  return rows.some(touched) || addedTerms(addText).length > 0;
}
