import { useEffect, useState } from "react";

/**
 * Debounces a fast-changing value, so a lookup fires once typing pauses
 * rather than once per keystroke (FR-52's spirit, applied to an interactive
 * caller rather than the batch sweep it was written for).
 *
 * Shared by `procedure-picker.tsx` (the SNOMED CT code search) and
 * `property-controls/concept-picker.tsx` (the coded-property picker). Both
 * debounce a typed value before it drives a network call, and the same
 * 400ms-then-fetch shape should not have two implementations.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeout);
  }, [value, delayMs]);
  return debounced;
}
