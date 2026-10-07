import type { components } from "../api/schema.ts";

type HistoryEvent = components["schemas"]["HistoryEvent"];

/** Audited columns that mean nothing to a reader: a row's position among a
 * property's values, and the form a term is compared in. */
const HIDDEN_FIELDS = new Set(["ordinal", "term_key"]);

/** Every audited foreign key ends in `_id` (`entry_id`,
 * `replaced_by_binding_id`, `acknowledged_by_user_id`). Matching the suffix
 * hides one added later without a list to keep in step. */
function isInternalKey(name: string): boolean {
  return name.endsWith("_id") || HIDDEN_FIELDS.has(name);
}

/** Field names whose plain capitalised form is unclear, an acronym, or not the
 * word the page uses for the same thing: the Details panel calls `business_key`
 * "Identifier", and the Terms table calls `use` "Type". */
const FIELD_LABELS: Record<string, string> = {
  business_key: "Identifier",
  fsn: "Fully specified name",
  au_preferred_term: "AU preferred term",
  property_key: "Property",
  system: "Code system",
  use: "Type",
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
 * a field is named. Internal keys are left out.
 */
export function describeChange(event: HistoryEvent): ChangeText {
  const fields = event.changed_fields
    .filter((field) => !isInternalKey(field))
    .map(fieldLabel);
  return {
    action: humanise(event.action.replaceAll(".", " ")),
    fields: fields.length > 0 ? fields.join(", ") : null,
  };
}
