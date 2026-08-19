/**
 * PKCE (RFC 7636) and the CSRF `state`, built on the Web Crypto API
 * (issue #41, NFR-01).
 *
 * `crypto.getRandomValues` and `crypto.subtle.digest`, never `Math.random`:
 * the `state` is the only thing standing between a user and a forged
 * callback, and the `code_verifier` is the only thing standing between an
 * intercepted authorisation code and a usable token. Both must be
 * unguessable.
 *
 * The backend's `backend/tests/test_keycloak_pkce_login.py::_pkce_pair`
 * performs the identical construction in Python and proves a real Keycloak
 * accepts it.
 */

/**
 * base64url per RFC 7636 Section 4.1: base64 with `+/` replaced by `-_` and
 * padding stripped. Keycloak compares the challenge as an exact string, so a
 * stray `=` here fails the exchange with `invalid_grant` and no useful
 * diagnostic.
 */
function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomBase64Url(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

/**
 * 64 bytes, well inside RFC 7636's 43-128 character range once base64url
 * encoded, and far above the 32 bytes of entropy the spec calls for.
 */
export function createCodeVerifier(): string {
  return randomBase64Url(64);
}

/** The CSRF token echoed back on the callback and checked there. */
export function createState(): string {
  return randomBase64Url(32);
}

/** Binds the ID token to this authorisation request (OIDC Core 3.1.2.1). */
export function createNonce(): string {
  return randomBase64Url(32);
}

/**
 * S256 only - never `plain`. The realm advertises S256 and
 * `nptc-frontend`'s `pkce.code.challenge.method` pins it (ADR-0014), so a
 * `plain` challenge would be refused anyway; not implementing it at all
 * means there is no downgrade path to reach for.
 */
export async function createCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64UrlEncode(new Uint8Array(digest));
}
