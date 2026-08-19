import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useAuth } from "../auth/session.ts";

/**
 * `/sign-out` (issue #41).
 *
 * Ends the local session immediately and then hands off to Keycloak's
 * `end_session_endpoint`, so the SSO session goes too. Ending only the local
 * one would leave the next visit to `/sign-in` silently re-authenticating
 * from a still-live SSO cookie - which is precisely what this issue's fifth
 * acceptance criterion rules out.
 *
 * Note the boundary ADR-0021 records: an access token already issued stays
 * valid until it expires (the realm's `accessTokenLifespan` is 300s). Fully
 * closing that window needs server-held sessions or per-request
 * introspection, neither of which this design has.
 */
export function SignOutPage() {
  const { status, signOut } = useAuth();
  // The redirect to Keycloak is a one-shot side effect; StrictMode's
  // double-invoked mount effect must not fire it twice.
  const started = useRef(false);

  useEffect(() => {
    if (started.current || status !== "signed-in") {
      return;
    }
    started.current = true;
    void signOut();
  }, [status, signOut]);

  if (status === "signed-in") {
    return (
      <section aria-labelledby="sign-out-heading">
        <h1 id="sign-out-heading">Signing you out</h1>
        <p>Ending your session. This should only take a moment.</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="sign-out-heading">
      <h1 id="sign-out-heading">You are signed out</h1>
      <p>Your session has ended. You can still browse the public catalogue.</p>
      <Link to="/catalogue">Search the catalogue</Link>
    </section>
  );
}
