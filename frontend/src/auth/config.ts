/**
 * Where the SPA finds Keycloak, and what it calls itself (issue #41).
 *
 * Both values are build-time `VITE_*` variables - the first in this repo.
 * That is safe precisely because ADR-0021's client is public: an issuer URL
 * and a client id are not secrets, and there is no third value that would be
 * (see `frontend/scripts/assert-no-secret-in-bundle.mjs`, which asserts that
 * against the built assets rather than trusting this comment).
 *
 * Missing configuration throws, naming the variable, rather than defaulting
 * to a plausible-looking localhost URL - the same fail-loud posture
 * `nptc.settings` takes on the backend. A silently defaulted issuer would
 * send users to an authorisation server nobody meant to trust.
 */

export interface AuthConfig {
  /** The realm's issuer URL, e.g. `http://localhost:8080/realms/nptc`. */
  issuer: string;
  clientId: string;
  /**
   * Derived from the current origin rather than configured: it must match
   * the realm's `redirectUris`, which are themselves derived from the one
   * `NPTC_FRONTEND_BASE_URL` placeholder (ADR-0014). A separately configured
   * value is a second source of truth that can only ever drift out of step
   * with the first and produce an "Invalid redirect_uri" at the worst moment.
   */
  redirectUri: string;
  postLogoutRedirectUri: string;
}

function required(name: string, value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed) {
    throw new Error(
      `${name} is not set - the sign-in flow cannot start without it. ` +
        `See docs/operations/configuration.md.`,
    );
  }
  return trimmed;
}

/** The callback path, which must stay in step with `route-tree.ts`. */
export const CALLBACK_PATH = "/auth/callback";

export function readAuthConfig(origin: string = window.location.origin): AuthConfig {
  return {
    // Trailing slashes are stripped so `${issuer}/.well-known/...` cannot
    // produce a double slash, which some reverse proxies do not normalise.
    issuer: required("VITE_OIDC_ISSUER", import.meta.env.VITE_OIDC_ISSUER).replace(
      /\/+$/,
      "",
    ),
    clientId: required("VITE_OIDC_CLIENT_ID", import.meta.env.VITE_OIDC_CLIENT_ID),
    redirectUri: `${origin}${CALLBACK_PATH}`,
    postLogoutRedirectUri: origin,
  };
}
