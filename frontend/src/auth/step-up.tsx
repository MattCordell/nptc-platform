import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import { SESSION_QUERY_KEY } from "../api/queries.ts";
import type {
  StepUpChallengeContext,
  StepUpChallengeHandler,
} from "../api/query-client.ts";
import type { StepUpChallenge } from "../api/step-up.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import { useAuth } from "./session.ts";

/**
 * The bridge between `createQueryClient`'s cache-level `onError` (built in
 * `main.tsx`/`render-route.tsx`, before any component - let alone one inside
 * `AuthProvider`/`RouterProvider` - exists to react to it) and whichever
 * `StepUpController` is currently mounted (issue #184, NFR-06).
 *
 * A module-level slot, not a prop threaded through the app tree: `main.tsx`
 * builds the `QueryClient` before `AuthProvider` or the router exist, and
 * `RootLayout` (where `StepUpController` mounts, so it can read `useAuth()`
 * and the router's current location) is instantiated by the router itself
 * with no channel for a caller-supplied prop. Test isolation instead comes
 * from `StepUpController`'s own mount/unmount effect below, the same
 * "cleared on the way out" precedent `nptc.api.dependencies.
 * get_terminology_client`'s `lru_cache` uses on the backend (`api_app_
 * support.py`'s `get_terminology_client.cache_clear()`): each test's render
 * unmounts before the next mounts, so the slot is never live across tests.
 */
let activeHandler: StepUpChallengeHandler | null = null;

/**
 * What `main.tsx` and `src/test/render-route.tsx` both pass to
 * `createQueryClient` - forwards to whichever `StepUpController` is
 * currently mounted, or does nothing before one has (the very first render,
 * or a narrow component test that mounts no router at all).
 */
export const stepUpChallengeHandler: StepUpChallengeHandler = (challenge, context) =>
  activeHandler?.(challenge, context) ?? Promise.resolve();

/**
 * `StepUpBanner`'s own entry point into the same controller (PR #284
 * review) - a `"no-retry"`-shaped context, since there is nothing to retry,
 * gets the identical silent-first/dialog/interactive-fallback treatment a
 * reactive challenge would, rather than the banner redirecting
 * unconditionally and unannounced the way it used to. A silent step-up that
 * succeeds now closes the banner with no navigation at all.
 *
 * Returns the attempt's own promise (PR #284 review round 2) so a caller -
 * today, only `StepUpBanner` - can show its own pending state (a disabled
 * button, a "Checking..." label) while a silent attempt is in flight, rather
 * than leaving a click with no feedback for up to `SILENT_RENEW_TIMEOUT_MS`.
 * The promise itself never rejects - see `handleChallenge` below - so
 * awaiting it needs no `catch`.
 */
export function requestStepUp(acrValues: string): Promise<void> {
  return activeHandler?.({ acrValues }, { kind: "no-retry" }) ?? Promise.resolve();
}

/**
 * Reacts to an RFC 9470 step-up challenge surfaced by `createQueryClient`
 * (issue #184, NFR-06): tries a silent re-authentication first, and only
 * asks the user to do anything if that cannot be satisfied without
 * interaction. Mounted once, inside `RootLayout` - every route needs the
 * same reaction, and mounting it there (rather than per-screen) is the
 * "detection lives at one seam" decision this issue's plan settled on.
 *
 * A **query** challenge is retried once the step-up succeeds, in place - no
 * navigation, no lost page state. A **no-retry** challenge - a refused
 * mutation, or `requestStepUp`'s pre-emptive one - is never replayed even on
 * a successful silent step-up (see `docs/adr/0036-spa-step-up-loop.md`): the
 * user resubmits, and by then MFA is satisfied.
 *
 * Retry-once, scoped to one challenge/retry cycle, not forever (PR #284
 * review): `attemptedQueryHashes` blocks a *second* challenge for the same
 * query while this cycle's own step-up attempt and retry are still in
 * flight, so the retried read's own failure cannot re-trigger a second
 * concurrent attempt - but the hash is removed once the cycle settles
 * (`finally`, below), so a genuinely later challenge (a second, unrelated
 * MFA requirement; the realm's `loa-max-age` elapsing) is not silently
 * swallowed. Not a retry counter on the query itself - `createQueryClient`'s
 * `retry: false` default is unaffected.
 */
export function StepUpController() {
  const { stepUp, signIn } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const attemptedQueryHashes = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // A ref alongside the router's own state, not a captured closure value:
  // `handleChallenge` must always redirect back to wherever the user
  // currently is, not wherever they were when this callback was last
  // recreated - and reading it via a ref keeps the callback's own identity
  // (and so the registration effect below) stable across navigation. Set in
  // an effect, not during render: a ref write belongs after commit, same as
  // `auth-context.tsx`'s own `tokensRef`.
  const currentHref = useRef(location.href);
  useEffect(() => {
    currentHref.current = location.href;
  });

  const [pendingInteractive, setPendingInteractive] = useState<{
    challenge: StepUpChallenge;
    redirect: string;
  } | null>(null);

  const handleChallenge = useCallback(
    (challenge: StepUpChallenge, context: StepUpChallengeContext): Promise<void> => {
      if (context.kind === "query") {
        if (attemptedQueryHashes.current.has(context.queryHash)) {
          return Promise.resolve();
        }
        attemptedQueryHashes.current.add(context.queryHash);
      }

      return (async () => {
        try {
          const outcome = await stepUp(challenge.acrValues);
          if (!mounted.current) {
            return;
          }
          if (outcome === "done") {
            // Not scoped to `context.kind === "query"`: a session that just
            // stepped up should stop `StepUpBanner` claiming otherwise
            // regardless of what triggered this attempt.
            void queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
            if (context.kind === "query") {
              await context.retry();
            }
            return;
          }
          // Interactive fallback needs the user told first (issue #184's own
          // acceptance criteria: an unannounced jump to an OTP prompt
          // mid-action is disorienting) - so this opens a dialog rather than
          // navigating immediately.
          setPendingInteractive({ challenge, redirect: currentHref.current });
        } catch {
          // Swallowed, not surfaced (PR #284 review round 2): `stepUp`
          // itself never throws, and `context.retry()` is today's only other
          // fallible call in this block - but neither this callback's
          // fire-and-forget caller (`createQueryClient`'s cache `onError`)
          // nor `requestStepUp`'s awaiting one has anywhere to put an error,
          // so a future fallible addition here fails closed (the dialog
          // simply does not open) rather than becoming an unhandled
          // rejection.
        } finally {
          if (context.kind === "query") {
            attemptedQueryHashes.current.delete(context.queryHash);
          }
        }
      })();
    },
    [stepUp, queryClient],
  );

  useEffect(() => {
    activeHandler = handleChallenge;
    return () => {
      // Guarded, not unconditional: an outgoing cleanup must only clear the
      // slot if it still owns it. An unconditional `activeHandler = null`
      // would let a mount that overlaps an unmount (a `RootLayout` remount,
      // a future second controller) have its registration wiped out by the
      // *outgoing* instance's cleanup, going silently dead with no error
      // and no failing test (PR #284 review).
      if (activeHandler === handleChallenge) {
        activeHandler = null;
      }
    };
  }, [handleChallenge]);

  const dismiss = useCallback(() => {
    setPendingInteractive(null);
  }, []);

  const proceed = useCallback(() => {
    if (!pendingInteractive) {
      return;
    }
    void signIn({
      acrValues: pendingInteractive.challenge.acrValues,
      redirect: pendingInteractive.redirect,
    });
  }, [pendingInteractive, signIn]);

  return (
    <Dialog
      open={pendingInteractive !== null}
      onClose={dismiss}
      title="Sign in again to continue"
    >
      <p>
        This action needs an extra sign-in step. You will be sent to sign in again, then
        brought back here to continue.
      </p>
      <div className="mt-4 flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={dismiss}>
          Cancel
        </Button>
        <Button type="button" variant="primary" onClick={proceed}>
          Continue
        </Button>
      </div>
    </Dialog>
  );
}
