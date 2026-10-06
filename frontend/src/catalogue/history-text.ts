import type { components } from "../api/schema.ts";

type HistoryEvent = components["schemas"]["HistoryEvent"];

/** Columns whose names say nothing a reader can use: the bookkeeping that
 * changes on every write, and the internal key that links a row to its entry. */
const HIDDEN_FIELDS = new Set(["row_version", "updated_at", "entry_id", "id"]);

/** Field names whose plain capitalised form would be an unexplained acronym. */
const FIELD_LABELS: Record<string, string> = {
  fsn: "Fully specified name",
  au_preferred_term: "AU preferred term",
};

/** `preferred_term` -> `Preferred term`. */
function humanise(name: string): string {
  const spaced = name.replaceAll("_", " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? humanise(name);
}

export interface ChangeText {
  /** What happened, from the internal action name: `designation.created` -> `Designation created`. */
  action: string;
  /** The field names that changed, in words, or `null` when none are worth naming. */
  fields: string | null;
}

/**
 * What a reader is told about one history event. The action name is turned into
 * a sentence rather than shown as stored, and the field names follow it as a
 * second line, so the action is never the only text a reader has to go on when
 * a field is named.
 */
export function describeChange(event: HistoryEvent): ChangeText {
  const fields = event.changed_fields
    .filter((field) => !HIDDEN_FIELDS.has(field))
    .map(fieldLabel);
  return {
    action: humanise(event.action.replaceAll(".", " ")),
    fields: fields.length > 0 ? fields.join(", ") : null,
  };
}
