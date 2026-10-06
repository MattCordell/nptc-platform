import type { components } from "../api/schema.ts";

type HistoryEvent = components["schemas"]["HistoryEvent"];

/** Bookkeeping columns that change on every write. Naming them says nothing a
 * reader can use. */
const HIDDEN_FIELDS = new Set(["row_version", "updated_at"]);

/** `preferred_term` -> `Preferred term`. */
function humanise(name: string): string {
  const spaced = name.replaceAll("_", " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * What a reader is told changed at one event. The changed field names come
 * first, because they say what was touched. With none left, the verb at the end
 * of the internal action name (`catalogue_entry.updated` -> `Updated`) is the
 * fallback: it is never the only text when a field is named.
 */
export function describeChange(event: HistoryEvent): string {
  const fields = event.changed_fields
    .filter((field) => !HIDDEN_FIELDS.has(field))
    .map(humanise);
  if (fields.length > 0) {
    return `Changed: ${fields.join(", ")}`;
  }
  return humanise(event.action.split(".").at(-1) ?? event.action);
}
