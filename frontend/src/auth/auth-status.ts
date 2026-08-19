import { useAuth, type AuthStatus } from "./session.ts";

/**
 * The one seam issue #41 replaced. It now reads the real OIDC session from
 * `AuthProvider` instead of returning a constant.
 *
 * Nothing here is a security boundary: NFR-20 requires every request to be
 * authorised server-side against the internal user record, and no
 * authorisation decision is ever made in the browser. This function only
 * decides what the shell *shows*; hiding a control is presentation, not
 * access control.
 *
 * Kept as its own module, with its return type unchanged, so the call sites
 * written against the placeholder (`require-auth.tsx`, `site-header.tsx`)
 * did not have to change when real sign-in landed.
 */
export type { AuthStatus };

export function useAuthStatus(): AuthStatus {
  return useAuth().status;
}
