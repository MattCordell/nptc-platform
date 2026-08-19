import { getRouteApi } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useAuth } from "../auth/session.ts";
import { asInternalRedirect } from "../router/search-params.ts";

/**
 * `getRouteApi`, not an import of the route object from `route-tree.ts`:
 * the route table imports this component, so importing the route back would
 * be a cycle. This resolves the route by id at call time and still gives
 * `useSearch` its validated `SignInSearch` type.
 */
const route = getRouteApi("/sign-in");

/**
 * `/sign-in` (issue #41). Starts the redirect to Keycloak.
 *
 * The redirect fires from an effect rather than during render because it is
 * a side effect on `window.location`, and React may render a component more
 * than once before committing (it does exactly that under `StrictMode`,
 * which `main.tsx` and the test harness both enable).
 *
 * The visible content is what a user sees for the fraction of a second
 * before the browser leaves, and what they are left with if the redirect
 * cannot happen at all - so it must stand on its own rather than say
 * "redirecting..." and nothing else.
 */
export function SignInPage() {
  const { status, signIn } = useAuth();
  // Re-checked here, not merely trusted from `validateSearch`. An
  // off-site `redirect` is an open redirect - a crafted `/sign-in?redirect=
  // https://evil.test` link would bounce a user off-site at the moment they
  // have just authenticated, which is exactly when they are least likely to
  // notice. Two independent checks is the right number for that, and it
  // costs one function call. `asInternalRedirect` is the same guard
  // `validateSignInSearch` applies, not a second implementation of it.
  const redirect = asInternalRedirect(route.useSearch().redirect);
  // One-shot: StrictMode double-invokes mount effects, and starting the
  // redirect twice would mint two PKCE transactions, the second of which
  // overwrites the first - so the callback would arrive carrying a `state`
  // the stored transaction no longer matches.
  const started = useRef(false);

  useEffect(() => {
    if (started.current || status !== "signed-out") {
      return;
    }
    started.current = true;
    void signIn({ redirect });
  }, [status, signIn, redirect]);

  if (status === "restoring") {
    // The cold-load probe has not answered yet. Starting an interactive
    // redirect now would throw away a session that is about to restore.
    return (
      <section aria-labelledby="sign-in-heading">
        <h1 id="sign-in-heading">Checking your session</h1>
        <p>One moment.</p>
      </section>
    );
  }

  if (status === "signed-in") {
    return (
      <section aria-labelledby="sign-in-heading">
        <h1 id="sign-in-heading">You are already signed in</h1>
        <p>Continue to the part of the platform you were heading for.</p>
      </section>
    );
  }

  if (status === "unavailable") {
    return (
      <section aria-labelledby="sign-in-heading">
        <h1 id="sign-in-heading">Sign-in is unavailable</h1>
        <p>
          The platform cannot reach the sign-in service at the moment. Try again in a few
          minutes; the public catalogue is still available meanwhile.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="sign-in-heading">
      <h1 id="sign-in-heading">Taking you to sign in</h1>
      <p>
        You are being sent to the NPTC sign-in service. If nothing happens, your browser
        may have blocked the redirect - reload this page to try again.
      </p>
    </section>
  );
}
