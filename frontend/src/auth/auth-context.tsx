import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";

import { readAuthConfig, type AuthConfig } from "./config.ts";
import {
  buildAuthorizeUrl,
  buildLogoutUrl,
  buildRegistrationUrl,
  completeSignIn,
  InteractionRequiredError,
  type TokenSet,
} from "./flow.ts";
import { AuthContext, type AuthContextValue } from "./session.ts";
import {
  silentAuthorize as defaultSilentAuthorize,
  type SilentAuthorize,
} from "./silent-renew.ts";
import { clearTransaction } from "./transaction.ts";

/**
 * The provider that owns the in-memory session (issue #41, ADR-0021).
 * The context object, its value type and the `useAuth` hook live in
 * `session.ts` so this module exports only a component - keeping
 * react-refresh able to hot-reload it.
 */

/**
 * Renew this long before `exp`. Keycloak's `accessTokenLifespan` is 300s, so
 * a 30s skew leaves ample room for a slow round trip without renewing on
 * almost every call.
 */
const RENEW_SKEW_MS = 30_000;

/** Real navigation away from the SPA - replaced in tests. */
const defaultNavigate = (url: string): void => {
  window.location.assign(url);
};

export function AuthProvider({
  children,
  config: providedConfig,
  silentAuthorize = defaultSilentAuthorize,
  navigate = defaultNavigate,
}: {
  children: ReactNode;
  /** Injected in tests; read from the environment in the real app. */
  config?: AuthConfig;
  /**
   * Injected in tests. jsdom performs no real navigation, so the real
   * iframe renewal can only ever hit its own timeout there.
   */
  silentAuthorize?: SilentAuthorize;
  /**
   * Leaving the app entirely, for the two redirects to Keycloak. Injected
   * in tests because jsdom implements `location.assign` as
   * non-configurable, so it cannot be spied on.
   */
  navigate?: (url: string) => void;
}) {
  const [tokens, setTokens] = useState<TokenSet | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  // A ref as well as state: `getAccessToken` must see the freshest token
  // without being re-created (and re-triggering effects) on every renewal.
  const tokensRef = useRef<TokenSet | null>(null);
  const renewal = useRef<Promise<TokenSet | null> | null>(null);

  const config = useMemo(() => {
    if (providedConfig) {
      return providedConfig;
    }
    try {
      return readAuthConfig();
    } catch {
      // A deployment with no OIDC configuration should render a shell that
      // says sign-in is unavailable, not a blank page from a thrown error
      // during the initial render.
      return null;
    }
  }, [providedConfig]);

  const store = useCallback((next: TokenSet | null) => {
    tokensRef.current = next;
    setTokens(next);
  }, []);

  const renew = useCallback(async (): Promise<TokenSet | null> => {
    if (!config) {
      return null;
    }
    // De-duplicated: several components asking for a token at once must
    // produce one renewal, not one each.
    renewal.current ??= (async () => {
      try {
        const url = await buildAuthorizeUrl(config, { prompt: "none" });
        const search = await silentAuthorize(url, config.redirectUri);
        const { tokens: next } = await completeSignIn(config, search);
        store(next);
        return next;
      } catch (error) {
        if (error instanceof InteractionRequiredError) {
          // Not a failure: the SSO session has ended, so the user is simply
          // signed out. This is the path a post-logout renewal takes.
          clearTransaction();
          store(null);
          return null;
        }
        clearTransaction();
        store(null);
        setUnavailable(true);
        return null;
      } finally {
        renewal.current = null;
      }
    })();
    return renewal.current;
  }, [config, store, silentAuthorize]);

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    const current = tokensRef.current;
    if (current && current.expiresAt - RENEW_SKEW_MS > Date.now()) {
      return current.accessToken;
    }
    return (await renew())?.accessToken ?? null;
  }, [renew]);

  const signIn = useCallback(
    async (options: { redirect?: string; acrValues?: string } = {}) => {
      if (!config) {
        return;
      }
      navigate(await buildAuthorizeUrl(config, options));
    },
    [config, navigate],
  );

  const register = useCallback(async () => {
    if (!config) {
      return;
    }
    navigate(await buildRegistrationUrl(config));
  }, [config, navigate]);

  const signOut = useCallback(async () => {
    const current = tokensRef.current;
    // Cleared *before* the redirect, not after: if the navigation to
    // Keycloak fails or the user cancels it, the local session must already
    // be gone. Ending the remote session without ending the local one is the
    // failure mode that leaves a "signed out" user still signed in.
    store(null);
    clearTransaction();
    if (!config || !current) {
      return;
    }
    navigate(await buildLogoutUrl(config, current.idToken));
  }, [config, store, navigate]);

  const restore = useCallback(async () => {
    if (!config || tokensRef.current) {
      return;
    }
    await renew();
  }, [config, renew]);

  const completeCallback = useCallback(
    async (search: URLSearchParams): Promise<string | null> => {
      if (!config) {
        return null;
      }
      try {
        const { tokens: next, redirect } = await completeSignIn(config, search);
        store(next);
        return redirect ?? "/";
      } catch {
        // Deliberately swallowed rather than surfaced: the message names
        // Keycloak's grant error, and `route-error-page.tsx`'s rule is that
        // a user-facing error says what to do next, never what the server
        // said. The caller renders that guidance.
        clearTransaction();
        return null;
      }
    },
    [config, store],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status:
        !config || unavailable ? "unavailable" : tokens ? "signed-in" : "signed-out",
      getAccessToken,
      signIn,
      signOut,
      register,
      restore,
      completeCallback,
    }),
    [
      config,
      unavailable,
      tokens,
      getAccessToken,
      signIn,
      signOut,
      register,
      restore,
      completeCallback,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
