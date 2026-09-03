import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CALLBACK_PATH, readAuthConfig, type AuthConfig } from "./config.ts";
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
import { clearTransactions, takeTransaction } from "./transaction.ts";

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
  //: False until the cold-load probe below has settled - see AuthStatus's
  //: `"restoring"`.
  const [restored, setRestored] = useState(false);
  // A ref as well as state: `getAccessToken` must see the freshest token
  // without being re-created (and re-triggering effects) on every renewal.
  const tokensRef = useRef<TokenSet | null>(null);
  const renewal = useRef<Promise<TokenSet | null> | null>(null);
  // Guards every setState call below so a probe or renewal still in flight
  // when this provider unmounts (e.g. between tests, or a remount
  // elsewhere in the app) can never touch a torn-down instance's state
  // (issue #243). This does not by itself stop the network/transaction
  // work such a probe is mid-flight on - `buildAuthorizeUrl`, the
  // `silentAuthorize` round trip, `completeSignIn` and `takeTransaction`
  // all still run to completion regardless, landing in the existing
  // `catch` below exactly as before. The `waitFor`s added to
  // `auth-context.test.tsx` alongside this guard, so the mount probe
  // settles inside the test that started it rather than outliving it, are
  // what actually closes the CI flake's window; this guard is the backstop
  // for whatever's still in flight despite that.
  const mounted = useRef(true);
  useEffect(() => {
    // Reset in the body, not only relied on as the `useRef` default:
    // `StrictMode` (main.tsx) double-invokes this exact mount -> cleanup ->
    // mount cycle, so a flag only ever cleared - never set back - by the
    // cleanup would end this effect permanently `false` after that cycle's
    // second mount, even though the provider is genuinely still mounted.
    //
    // That second mount's own effect body is a no-op, though (see `probed`
    // below): the probe actually running to completion is the one the
    // *first*, discarded-by-StrictMode mount started. It survives because
    // `probed`, `mounted` and every ref/callback it closes over live on the
    // one fiber React reuses across the double-invoke, not on either
    // individual mount - so resetting `mounted.current` here is what lets
    // that surviving probe keep being treated as "mounted" for the rest of
    // its genuinely-single lifetime. A future change that made `probed`
    // reset on cleanup (so the second mount re-probed) would need this
    // reset to move with it.
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

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
    if (next) {
      // Cleared on success, not only set on failure: `unavailable` was
      // otherwise permanent for the life of the tab, so one transient
      // network blip degraded the shell for a user who then signed in
      // perfectly well.
      setUnavailable(false);
    }
  }, []);

  const renew = useCallback(async (): Promise<TokenSet | null> => {
    if (!config) {
      return null;
    }
    // De-duplicated: several components asking for a token at once must
    // produce one renewal, not one each.
    renewal.current ??= (async () => {
      // Remembered so a failure cleans up *this* renewal's transaction and
      // nothing else. Clearing every transaction here would discard a
      // concurrent interactive sign-in's, failing a sign-in that worked -
      // which is the whole reason transactions are keyed by state.
      let issuedState: string | null = null;
      // What this renewal is refreshing. `store` assigns `tokensRef.current`
      // synchronously (see `store` above), so comparing against it after an
      // `await` tells us whether anything else - a `completeCallback` that
      // raced this renewal - already changed the session. If it did, this
      // renewal's refusal says nothing about that session and must not
      // clear it (issue #216).
      const startedWith = tokensRef.current;
      try {
        const url = await buildAuthorizeUrl(config, { prompt: "none" });
        issuedState = new URL(url).searchParams.get("state");
        const search = await silentAuthorize(url, config.redirectUri);
        const { tokens: next } = await completeSignIn(config, search);
        // A provider that unmounted while this renewal was in flight has no
        // state left worth touching - checked ahead of the #216 guard
        // below, not instead of it (issue #243). Deliberately returns
        // `null` here rather than `next`: a caller still awaiting this same
        // `renewal.current` (e.g. a concurrent `getAccessToken`) gets told
        // "no token" even though one was in fact obtained, and falls back
        // to `tokensRef.current`, which this skipped `store(next)` also
        // never updated - so it sees the pre-renewal token, not the new
        // one. Harmless in practice: a provider that unmounted has no live
        // caller left to hand a token to, and the alternative (calling
        // `store` anyway, unguarded, so late callers get the right value)
        // is exactly the post-unmount side effect this guard exists to
        // prevent.
        if (!mounted.current) {
          return null;
        }
        // Symmetric with the failure-path guard below: this renewal only
        // knows about the session it started with, so it must not install
        // its own late answer over whatever `tokensRef.current` has become
        // in the meantime - most concretely, a `completeCallback` sign-in
        // that changed `null` to a session while this renewal was in
        // flight. Note this is an identity comparison, so it only detects
        // a value that has *changed*; it cannot tell "still null" apart
        // from "cleared and set back to null", so it does not guard, say,
        // a `signOut` that found no session to end (tokens were null both
        // before and after).
        if (tokensRef.current !== startedWith) {
          return null;
        }
        store(next);
        return next;
      } catch (error) {
        // Runs regardless of `mounted` - deliberately the one exception to
        // "no side effects after unmount": this is cleanup of a
        // module-level transaction, not provider state, and skipping it
        // post-unmount would leak the entry rather than merely acting on a
        // torn-down instance.
        if (issuedState) {
          takeTransaction(issuedState);
        }
        if (!mounted.current) {
          return null;
        }
        const stillOurs = tokensRef.current === startedWith;
        if (error instanceof InteractionRequiredError) {
          // Not a failure: the SSO session has ended, so the user is simply
          // signed out. This is the path a post-logout renewal takes.
          if (stillOurs) {
            store(null);
          }
          return null;
        }
        if (stillOurs) {
          store(null);
          setUnavailable(true);
        }
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
    const renewed = await renew();
    // `renew()`'s own return value can be a stale `null`: a refused renewal
    // that raced a concurrent `completeCallback` leaves that session in
    // place (see the `stillOurs` guard above) but still resolves `null`
    // itself. Falling back to the ref picks up whatever session actually
    // ended up in place, rather than handing this caller a false "signed
    // out" (issue #216).
    return renewed?.accessToken ?? tokensRef.current?.accessToken ?? null;
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
    clearTransactions();
    // An explicit sign-out is a definitive answer about the session, so it
    // settles `"restoring"` too - otherwise a user who signs out before the
    // cold-load probe returns is left on a "checking your session" screen.
    setRestored(true);
    if (!config || !current) {
      return;
    }
    navigate(await buildLogoutUrl(config, current.idToken));
  }, [config, store, navigate]);

  const restore = useCallback(async () => {
    try {
      // Yield first, so every `setRestored` below lands after the calling
      // effect's synchronous phase rather than during it - a synchronous
      // setState inside an effect triggers a cascading render.
      await Promise.resolve();
      if (!config || tokensRef.current) {
        return;
      }
      // Not on the callback route: the code exchange about to run there is
      // what establishes the session, and a concurrent renewal would be a
      // second authorize round trip racing it for no benefit.
      if (window.location.pathname === CALLBACK_PATH) {
        return;
      }
      await renew();
    } finally {
      // In a `finally`, so a thrown probe cannot strand the whole app in
      // `"restoring"` - a status nothing would ever move it out of. Guarded
      // by `mounted` for the same reason as `renew()`'s own side effects: a
      // provider that unmounted before this settles has nothing left to
      // move out of `"restoring"` (issue #243).
      if (mounted.current) {
        setRestored(true);
      }
    }
  }, [config, renew]);

  // The cold-load probe, run once. Inside the provider rather than a
  // sibling component so that `"restoring"` is always resolved by whoever
  // owns it: a provider that depended on someone else remembering to mount
  // a probe would hang in `"restoring"` when they forgot.
  const probed = useRef(false);
  useEffect(() => {
    if (probed.current) {
      return;
    }
    probed.current = true;
    void restore();
  }, [restore]);

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
        //
        // No cleanup here: `completeSignIn` consumes the transaction on
        // every path it can fail on, and clearing the rest would take a
        // concurrent renewal's with it.
        return null;
      }
    },
    [config, store],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      // Order matters: an unusable configuration or an unreachable provider
      // outranks everything (the user cannot fix it by signing in), a token
      // means signed in, and "signed-out" is only asserted once the
      // cold-load probe has actually answered.
      status:
        !config || unavailable
          ? "unavailable"
          : tokens
            ? "signed-in"
            : restored
              ? "signed-out"
              : "restoring",
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
      restored,
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
