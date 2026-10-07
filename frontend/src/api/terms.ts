import type { components } from "./schema.ts";
import { ApiError } from "./unwrap.ts";

/**
 * Recognising the two refusals terms acceptance adds (NFR-45, NFR-47,
 * ADR-0043). Both are told apart by `code`, never by `detail`: `detail` is a
 * sentence for people and the server may reword it, while `code` is the
 * contract. A status alone is not enough either - a 403 may be a missing
 * permission or a step-up challenge, and a 409 may be an FR-38 version
 * conflict, so each guard also checks the discriminating `code`.
 */

export type TermsRequiredBody = components["schemas"]["TermsAcceptanceRequiredResponse"];
export type TermsVersionStaleBody = components["schemas"]["TermsVersionStaleResponse"];

/**
 * The prefix of every cached read of the current terms. One export, so the
 * hook that reads the terms, the mutation that accepts them and the cache
 * handler that reacts to a refused write all invalidate the same key.
 */
export const TERMS_QUERY_KEY = ["api", "/api/v1/auth/terms"] as const;

function codedBody(error: unknown, status: number): Record<string, unknown> | null {
  if (!(error instanceof ApiError) || error.status !== status) {
    return null;
  }
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  return error.body as Record<string, unknown>;
}

/**
 * The 403 for a write from a user who has not accepted the current terms, or
 * `null` if this refusal is anything else. Carries no `WWW-Authenticate`, so
 * it never reaches the step-up path.
 */
export function asTermsRequired(error: unknown): TermsRequiredBody | null {
  const body = codedBody(error, 403);
  if (body === null || body.code !== "terms_acceptance_required") {
    return null;
  }
  return body as unknown as TermsRequiredBody;
}

/**
 * The 409 for an accept request naming a version that is no longer current,
 * or `null` if this refusal is anything else. `current_version` is required
 * so a body missing it falls back to the plain sentence rather than showing
 * the gate under an undefined version.
 */
export function asTermsVersionStale(error: unknown): TermsVersionStaleBody | null {
  const body = codedBody(error, 409);
  if (
    body === null ||
    body.code !== "terms_version_stale" ||
    typeof body.current_version !== "string"
  ) {
    return null;
  }
  return body as unknown as TermsVersionStaleBody;
}
