/**
 * The authorisation-code + PKCE flow itself (issue #41, NFR-01).
 *
 * The browser performs the exchange because `nptc-frontend` is a public
 * client with no secret to protect (ADR-0021). What the browser must *not*
 * do is decide anything about authorisation: the token is opaque here, and
 * every permission question is answered by the API (NFR-20).
 */

import type { AuthConfig } from "./config.ts";
import { loadEndpoints } from "./discovery.ts";
import {
  createCodeChallenge,
  createCodeVerifier,
  createNonce,
  createState,
} from "./pkce.ts";
import { saveTransaction, takeTransaction } from "./transaction.ts";

/** What a successful exchange yields. Deliberately no refresh token. */
export interface TokenSet {
  accessToken: string;
  idToken: string;
  /** Epoch milliseconds, computed from `expires_in` at receipt. */
  expiresAt: number;
}

export class AuthFlowError extends Error {}

/**
 * Raised when Keycloak answers a `prompt=none` renewal with
 * `login_required`. Distinct from `AuthFlowError` because it is not a
 * failure: it is the expected answer once the SSO session has ended, and
 * the caller's correct response is to become signed-out quietly rather than
 * to show an error.
 */
export class InteractionRequiredError extends AuthFlowError {}

export interface BeginSignInOptions {
  /** Internal path to return to, already validated by `search-params.ts`. */
  redirect?: string;
  /**
   * `"none"` for a silent renewal. Anything else is left to the realm's
   * default, which shows the login form when there is no live SSO session.
   */
  prompt?: "none";
  /**
   * The step-up level (NFR-06). The realm's LoA-2 subflow requires OTP when
   * this is `"2"`; ordinary sign-in omits it and is satisfied by the LoA-1
   * password subflow.
   */
  acrValues?: string;
}

/**
 * Builds the authorize URL and stores the transaction that
 * `completeSignIn` will need. Returns the URL rather than navigating, so
 * the caller decides between a top-level redirect and a hidden iframe, and
 * so this stays testable without a real navigation.
 */
export async function buildAuthorizeUrl(
  config: AuthConfig,
  options: BeginSignInOptions = {},
): Promise<string> {
  const { authorizationEndpoint } = await loadEndpoints(config.issuer);
  const codeVerifier = createCodeVerifier();
  const state = createState();
  const nonce = createNonce();

  saveTransaction({ state, codeVerifier, nonce, redirect: options.redirect });

  const url = new URL(authorizationEndpoint);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid profile email");
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("state", state);
  url.searchParams.set("nonce", nonce);
  url.searchParams.set("code_challenge", await createCodeChallenge(codeVerifier));
  url.searchParams.set("code_challenge_method", "S256");
  if (options.prompt) {
    url.searchParams.set("prompt", options.prompt);
  }
  if (options.acrValues) {
    url.searchParams.set("acr_values", options.acrValues);
  }
  return url.toString();
}

export interface CompletedSignIn {
  tokens: TokenSet;
  /** The path the user was heading for when sign-in interrupted them. */
  redirect?: string;
}

/**
 * Validates the callback and exchanges the code for tokens.
 *
 * The `state` check is the reason this function exists as a unit: it is
 * performed against the stored transaction *before* the code is sent
 * anywhere, and the transaction is consumed in the process
 * (`takeTransaction`), so a replayed callback finds no transaction and is
 * refused without a network call.
 */
export async function completeSignIn(
  config: AuthConfig,
  search: URLSearchParams,
): Promise<CompletedSignIn> {
  const transaction = takeTransaction();

  const error = search.get("error");
  if (error) {
    if (error === "login_required" || error === "interaction_required") {
      throw new InteractionRequiredError(error);
    }
    throw new AuthFlowError(`the identity provider refused the request (${error})`);
  }

  if (!transaction) {
    throw new AuthFlowError(
      "no sign-in was in progress in this tab - the link may have been reused or reloaded",
    );
  }

  const state = search.get("state");
  if (!state) {
    throw new AuthFlowError("the callback carried no state parameter");
  }
  if (state !== transaction.state) {
    throw new AuthFlowError("the callback's state did not match the request");
  }

  const code = search.get("code");
  if (!code) {
    throw new AuthFlowError("the callback carried no authorisation code");
  }

  return {
    tokens: await exchangeCode(config, code, transaction.codeVerifier),
    redirect: transaction.redirect,
  };
}

async function exchangeCode(
  config: AuthConfig,
  code: string,
  codeVerifier: string,
): Promise<TokenSet> {
  const { tokenEndpoint } = await loadEndpoints(config.issuer);
  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    // No `client_secret`: the whole point of PKCE for a public client. The
    // built bundle is asserted to contain none - see
    // frontend/scripts/assert-no-secret-in-bundle.mjs.
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
      code_verifier: codeVerifier,
    }),
  });

  if (!response.ok) {
    // The body carries Keycloak's `error`/`error_description`, which names
    // the grant but never a credential. It is deliberately not surfaced to
    // the user - `route-error-page.tsx`'s rule is that an error screen says
    // what to do next, not what the server said.
    throw new AuthFlowError(`the token exchange was refused (HTTP ${response.status})`);
  }

  const body = (await response.json()) as Record<string, unknown>;
  const accessToken = body.access_token;
  const idToken = body.id_token;
  if (typeof accessToken !== "string" || typeof idToken !== "string") {
    throw new AuthFlowError("the token response was missing a token");
  }
  const expiresIn = typeof body.expires_in === "number" ? body.expires_in : 0;

  return { accessToken, idToken, expiresAt: Date.now() + expiresIn * 1000 };
}

/**
 * The RP-initiated logout URL (OIDC RP-Initiated Logout 1.0).
 *
 * `id_token_hint` is what lets Keycloak end the *right* session without
 * prompting. Without it the user is shown a "do you want to log out?"
 * confirmation, and a logout the user has already asked for should not need
 * asking twice.
 */
export async function buildLogoutUrl(
  config: AuthConfig,
  idToken: string,
): Promise<string> {
  const { endSessionEndpoint } = await loadEndpoints(config.issuer);
  const url = new URL(endSessionEndpoint);
  url.searchParams.set("id_token_hint", idToken);
  url.searchParams.set("post_logout_redirect_uri", config.postLogoutRedirectUri);
  return url.toString();
}

/** Keycloak's registration page for this client (NFR-02). */
export async function buildRegistrationUrl(config: AuthConfig): Promise<string> {
  const authorizeUrl = new URL(await buildAuthorizeUrl(config));
  // Keycloak exposes registration as a sibling of the authorize endpoint,
  // taking the identical parameters - so the user lands back in the same
  // flow, with the same PKCE transaction, once they have registered.
  authorizeUrl.pathname = authorizeUrl.pathname.replace(/\/auth$/, "/registrations");
  return authorizeUrl.toString();
}
