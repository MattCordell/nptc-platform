import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth/session.ts";

/**
 * `/auth/callback` (issue #41). Completes the exchange Keycloak redirected
 * back into, then sends the user where they were going.
 *
 * All of the checking lives in `auth-context`/`flow.ts`; this component's
 * only jobs are to run it once and to say something useful if it fails.
 */
export function AuthCallbackPage() {
  const { completeCallback } = useAuth();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  // StrictMode double-invokes mount effects, and the exchange is
  // single-use by design (the transaction is consumed, and Keycloak refuses
  // a replayed code). Without this guard the second invocation would always
  // "fail" and show an error over a sign-in that actually worked.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;

    void (async () => {
      const redirect = await completeCallback(
        new URLSearchParams(window.location.search),
      );
      if (redirect === null) {
        setFailed(true);
        return;
      }
      // `replace` so the callback URL - which carries a spent code - never
      // becomes a back-button destination.
      void navigate({ to: redirect, replace: true });
    })();
  }, [completeCallback, navigate]);

  if (failed) {
    return (
      <section aria-labelledby="callback-heading">
        <h1 id="callback-heading">Sign-in could not be completed</h1>
        <p>
          This can happen if the sign-in link was reused, opened in a different tab, or
          left too long before finishing. Start again from the sign-in page.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="callback-heading">
      <h1 id="callback-heading">Signing you in</h1>
      <p>Finishing sign-in. This should only take a moment.</p>
    </section>
  );
}
