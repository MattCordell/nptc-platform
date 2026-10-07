/**
 * Display text for the registry's closed value sets (FR-08, FR-10). Each helper
 * falls back to the raw wire value for one it does not list, because the API
 * types these fields as plain strings and a screen should degrade to showing
 * the value rather than a blank.
 *
 * Nothing here looks at a property's datatype: that dispatch belongs to the
 * registry's datatype handlers (FR-77, ADR-0013).
 */

const SCOPE_LABELS: Record<string, string> = {
  submission: "Submission",
  maintenance: "Maintenance",
  both: "Both",
};

const CARDINALITY_LABELS: Record<string, string> = {
  "0..1": "Zero or one",
  "1..1": "Exactly one",
  "0..*": "Zero or more",
  "1..*": "One or more",
};

const ORIGIN_LABELS: Record<string, string> = {
  system: "System",
  admin: "Administrator",
};

const BINDING_TARGET_LABELS: Record<string, string> = {
  value_set: "Value set",
  local_code_system: "Local code system",
};

const STRENGTH_LABELS: Record<string, string> = {
  required: "Required",
  extensible: "Extensible",
  example: "Example",
};

export function scopeLabelFor(scope: string): string {
  return SCOPE_LABELS[scope] ?? scope;
}

export function cardinalityLabelFor(cardinality: string): string {
  return CARDINALITY_LABELS[cardinality] ?? cardinality;
}

export function originLabelFor(origin: string): string {
  return ORIGIN_LABELS[origin] ?? origin;
}

export function bindingTargetLabelFor(target: string): string {
  return BINDING_TARGET_LABELS[target] ?? target;
}

export function bindingStrengthLabelFor(strength: string): string {
  return STRENGTH_LABELS[strength] ?? strength;
}

/**
 * A `constraints` value as text. A string shows as itself, and anything else
 * (a number, a list) as JSON, so the screen needs no knowledge of which
 * constraint keys a datatype defines.
 */
export function constraintValueText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
