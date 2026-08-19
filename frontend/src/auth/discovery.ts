/**
 * The realm's OIDC discovery document (issue #41).
 *
 * The three endpoints the SPA needs are read from the realm rather than
 * hand-built from the issuer URL. Keycloak's paths are stable in practice,
 * but a hand-built `${issuer}/protocol/openid-connect/auth` is a second
 * copy of a contract the server already publishes, and it is the copy that
 * silently breaks if the realm ever sits behind a path-rewriting proxy.
 *
 * Cached for the lifetime of the page: the document is immutable in
 * practice, and re-fetching it on every silent renewal would add a round
 * trip to something that is meant to be invisible.
 */

export interface OidcEndpoints {
  authorizationEndpoint: string;
  tokenEndpoint: string;
  endSessionEndpoint: string;
}

let cached: Promise<OidcEndpoints> | null = null;

function requireString(document: Record<string, unknown>, key: string): string {
  const value = document[key];
  if (typeof value !== "string" || !value) {
    throw new Error(`OIDC discovery document has no usable ${key}`);
  }
  return value;
}

async function fetchEndpoints(issuer: string): Promise<OidcEndpoints> {
  const response = await fetch(`${issuer}/.well-known/openid-configuration`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`OIDC discovery failed with HTTP ${response.status}`);
  }
  const document = (await response.json()) as Record<string, unknown>;

  // The document must describe the issuer we asked about. Without this a
  // misconfigured or substituted document could point the login redirect at
  // an entirely different authorisation server - and the user would see a
  // convincing login form belonging to someone else. The backend's
  // `nptc.auth.discovery` makes the same check for the same reason.
  if (document.issuer !== issuer) {
    throw new Error(
      `OIDC discovery document names issuer ${String(document.issuer)}, expected ${issuer}`,
    );
  }

  return {
    authorizationEndpoint: requireString(document, "authorization_endpoint"),
    tokenEndpoint: requireString(document, "token_endpoint"),
    endSessionEndpoint: requireString(document, "end_session_endpoint"),
  };
}

export function loadEndpoints(issuer: string): Promise<OidcEndpoints> {
  // A failed lookup is not cached: a discovery call that failed because
  // Keycloak was still starting must not poison every later sign-in attempt
  // for the lifetime of the tab.
  cached ??= fetchEndpoints(issuer).catch((error: unknown) => {
    cached = null;
    throw error;
  });
  return cached;
}

/** Test-only: drops the module-level cache between cases. */
export function resetEndpointCache(): void {
  cached = null;
}
