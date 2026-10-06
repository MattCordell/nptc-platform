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
