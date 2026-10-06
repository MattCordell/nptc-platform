const SNOMED_SYSTEM = "http://snomed.info/sct";

/**
 * `PropertyValue.value` is `unknown` in the schema - every control today
 * emits a string, number or boolean, but `String({})` reading
 * `"[object Object]"` for a future object-valued datatype would be a worse
 * failure than an explicit JSON fallback. A string is returned untouched, so a
 * code keeps its leading zeros and every digit (FR-06).
 */
export function formatPropertyValue(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  return JSON.stringify(value);
}

/** What the API serves for a coded value: the code, the system it belongs to,
 * and the term recorded with it. */
interface CodedValue {
  code: string;
  system: string;
  display: string | null;
}

function isCodedValue(value: unknown): value is CodedValue {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { code, system, display } = value as Record<string, unknown>;
  return (
    typeof code === "string" &&
    typeof system === "string" &&
    (display === null || display === undefined || typeof display === "string")
  );
}

export interface PropertyValueView {
  /** The words to show; empty when only a code is to be shown. */
  text: string;
  /** A SNOMED CT code to show in a code chip, or `null`. */
  snomedCode: string | null;
}

/**
 * How one property value reads on the public page. A coded value shows its
 * recorded term, and its code too when it is a SNOMED CT code, since that is
 * the part a reader compares against another system (FR-06). A local code
 * shows only its term. Any other value is formatted as `formatPropertyValue`
 * does.
 *
 * It looks at the shape of the value, never at the property's `datatype`:
 * branching on that is what ADR-0013 forbids.
 */
export function describePropertyValue(value: unknown): PropertyValueView {
  if (!isCodedValue(value)) {
    return { text: formatPropertyValue(value), snomedCode: null };
  }
  const snomedCode = value.system === SNOMED_SYSTEM ? value.code : null;
  if (value.display !== null && value.display !== undefined) {
    return { text: value.display, snomedCode };
  }
  return { text: snomedCode === null ? value.code : "", snomedCode };
}
