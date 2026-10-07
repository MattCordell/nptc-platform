import { refusalDetail } from "../api/conflicts.ts";
import { ApiError } from "../api/unwrap.ts";

/**
 * What the registry screens say when a property will not load. One string each,
 * because the screen both shows and announces it and the two must not drift.
 */

export function propertyStaleWarning(key: string): string {
  return (
    `${key} could not be refreshed just now, so what follows may be out of date. ` +
    "Reload the page to try again."
  );
}

export function propertyLoadFailureMessage(key: string, error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return `No property was found for ${key}. Check the key.`;
  }
  return (
    refusalDetail(error) ??
    `${key} could not be loaded. Try again, or contact an administrator if the problem persists.`
  );
}
