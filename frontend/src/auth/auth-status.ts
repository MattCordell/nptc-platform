/**
 * The one seam issue #41 (OIDC PKCE login) replaces.
 *
 * Nothing here is a security boundary: NFR-20 requires every request to be
 * authorised server-side against the internal user record, and no
 * authorisation decision is ever made in the browser. This function only
 * decides what the shell *shows*; hiding a control is presentation, not
 * access control.
 *
 * Keeping this in its own module (rather than inline in `RequireAuth`) means
 * #41's diff touches `src/auth/`, not `src/router/` or `src/shell/` - the
 * route table and the layout shell do not change when real sign-in lands.
 */
export type AuthStatus = "signed-in" | "signed-out" | "unavailable";

export function useAuthStatus(): AuthStatus {
  return "unavailable";
}
