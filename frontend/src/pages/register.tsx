import { useEffect, useRef } from "react";

import { useAuth } from "../auth/session.ts";

/**
 * `/register` (issue #41, NFR-02).
 *
 * Registration is Keycloak's own page, not a form here: NFR-02 puts the
 * local user database and ordinary username/password registration in the
 * realm, and re-implementing the form would mean this application handling
 * credentials - which NFR-01 says it never does.
 *
 * Because Keycloak's registration endpoint takes the same parameters as the
 * authorize endpoint, a user who registers lands straight back in the same
 * PKCE flow and arrives signed in, with no second trip through `/sign-in`.
 *
 * Keycloak's page shows the collection notice and links to `/terms` and
 * `/privacy`. It records no acceptance: the terms gate in the signed-in
 * routes asks for that on first sign-in (NFR-45, ADR-0043).
 */
export function RegisterPage() {
  const { status, register } = useAuth();
  const started = useRef(false);

  useEffect(() => {
    if (started.current || status !== "signed-out") {
      return;
    }
    started.current = true;
    void register();
  }, [status, register]);

  if (status === "restoring") {
    // The cold-load probe has not answered yet. Starting an interactive
    // redirect now would throw away a session that is about to restore.
    return (
      <section aria-labelledby="register-heading">
        <h1 id="register-heading">Checking your session</h1>
        <p>One moment.</p>
      </section>
    );
  }

  if (status === "signed-in") {
    return (
      <section aria-labelledby="register-heading">
        <h1 id="register-heading">You already have an account</h1>
        <p>You are signed in, so there is nothing to register.</p>
      </section>
    );
  }

  if (status === "unavailable") {
    return (
      <section aria-labelledby="register-heading">
        <h1 id="register-heading">Registration is unavailable</h1>
        <p>
          The platform cannot reach the sign-in service at the moment. Try again in a few
          minutes.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="register-heading">
      <h1 id="register-heading">Taking you to registration</h1>
      <p>
        You are being sent to the NPTC sign-in service to create an account. If nothing
        happens, your browser may have blocked the redirect - reload this page to try
        again.
      </p>
    </section>
  );
}
