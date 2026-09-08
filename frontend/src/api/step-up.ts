import { ApiError } from "./unwrap.ts";

/**
 * Parsing and recognising the RFC 9470 step-up challenge the backend sends
 * on a 403 whose refusal is `insufficient_user_authentication` (issue #184,
 * NFR-06) - `nptc.api.errors._step_up_challenge`.
 *
 * A challenge looks like:
 *
 *     WWW-Authenticate: Bearer error="insufficient_user_authentication", acr_values="2"
 *
 * `acr_values` is read from the header rather than hard-coded, so changing
 * the realm's LoA mapping (`AuthSettings.mfa_acr_values`) needs no frontend
 * change either.
 */

export interface StepUpChallenge {
  /** The LoA(s) that would satisfy the challenge, e.g. `"2"`. */
  acrValues: string;
}

const CHALLENGE_ERROR = "insufficient_user_authentication";

/**
 * A minimal `Bearer <param>=<value>, ...` parser - not a general RFC 9110
 * `WWW-Authenticate` parser (no multiple challenges, no unquoted tokens):
 * this backend only ever emits the one shape above, and a full parser would
 * be speculative generality for a header this codebase controls both ends
 * of.
 */
function parseBearerParams(headerValue: string): Map<string, string> | null {
  const match = /^Bearer\s+(.*)$/i.exec(headerValue.trim());
  if (!match) {
    return null;
  }
  const params = new Map<string, string>();
  const paramPattern = /([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"/g;
  let paramMatch: RegExpExecArray | null;
  while ((paramMatch = paramPattern.exec(match[1])) !== null) {
    params.set(paramMatch[1], paramMatch[2]);
  }
  return params;
}

/**
 * Parses a `WWW-Authenticate` header value into a step-up challenge, or
 * `null` if it is not one - a plain `Bearer` 401 challenge and an ordinary
 * `error="insufficient_scope"` refusal both parse to `null` here, same as a
 * header this backend never sends.
 */
export function parseStepUpChallenge(headerValue: string | null): StepUpChallenge | null {
  if (headerValue === null) {
    return null;
  }
  const params = parseBearerParams(headerValue);
  if (params === null) {
    return null;
  }
  if (params.get("error") !== CHALLENGE_ERROR) {
    return null;
  }
  const acrValues = params.get("acr_values");
  if (!acrValues) {
    return null;
  }
  return { acrValues };
}

/**
 * The step-up challenge carried by a failed API call, or `null` if this
 * refusal is not one - an ordinary 403 for a missing permission (FR-44's
 * negative case) has no `WWW-Authenticate` header at all and must not be
 * sent through step-up (its own test, per the issue #184 acceptance
 * criteria).
 *
 * Checks `error.status` **and** the discriminating `error` parameter, the
 * same status-plus-discriminator discipline `conflicts.ts` applies to its
 * 409s: a 403 alone is not enough to know *why* it was refused.
 */
export function asStepUpChallenge(error: unknown): StepUpChallenge | null {
  if (!(error instanceof ApiError) || error.status !== 403) {
    return null;
  }
  return parseStepUpChallenge(error.headers.get("WWW-Authenticate"));
}
