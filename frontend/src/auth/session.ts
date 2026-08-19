import { createContext, useContext } from "react";

/**
 * The session, held **in memory only** (issue #41, ADR-0021).
 *
 * Nothing here is written to `localStorage` or `sessionStorage`. That is the
 * deliberate trade ADR-0021 records: a token in memory dies with the tab, so
 * a stolen storage entry cannot outlive the session, at the cost of a silent
 * `prompt=none` round trip on every cold load. A refresh token is never
 * requested at all - it would be the one long-lived credential worth
 * stealing via XSS, and `prompt=none` against the SSO cookie does the same
 * job without one.
 *
 * This is not a security boundary. NFR-20 puts every authorisation decision
 * server-side; what this context decides is only what the shell *shows*.
 */

/**
 * `"restoring"` is the initial status on every cold load, and it is
 * load-bearing rather than cosmetic. Tokens live in memory only, so a fresh
 * page starts with none even when the Keycloak SSO cookie is perfectly
 * valid; without a status distinct from `"signed-out"`, `RequireAuth` would
 * redirect and `/sign-in` would start a full interactive round trip in the
 * moment before the silent renewal answered - so deep-linking into an
 * authenticated screen with a live session would leave the SPA entirely.
 * Both treat `"restoring"` as "wait".
 */
export type AuthStatus = "restoring" | "signed-in" | "signed-out" | "unavailable";

export interface AuthContextValue {
  status: AuthStatus;
  /**
   * A valid access token, renewing it first if it is at or near expiry, or
   * `null` when the user is not signed in. Async because a renewal may be
   * needed - callers must always await this rather than caching the string.
   */
  getAccessToken: () => Promise<string | null>;
  signIn: (options?: { redirect?: string; acrValues?: string }) => Promise<void>;
  signOut: () => Promise<void>;
  register: () => Promise<void>;
  /**
   * Cold-load probe: attempts a silent renewal, resolving either way, and
   * settling `status` out of `"restoring"`. `AuthProvider` runs this once
   * on mount; it is exposed for tests and for a manual retry.
   */
  restore: () => Promise<void>;
  /**
   * Completes a callback, returning the internal path to continue to, or
   * `null` if the callback was refused. A returned `null` is a normal
   * outcome the caller renders, not an exception to catch - the refusals
   * (a replayed link, a mismatched `state`) are things users hit by
   * accident, not programming errors.
   */
  completeCallback: (search: URLSearchParams) => Promise<string | null>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth was called outside an <AuthProvider>");
  }
  return value;
}
