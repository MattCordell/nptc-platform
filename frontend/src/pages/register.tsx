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
 * **Not yet covered**: NFR-14/NFR-45 require the privacy notice and
 * versioned terms to be presented with positive acceptance at registration.
 * Handing off to Keycloak's page does not do that - see the follow-up issue
 * linked from this PR.
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
