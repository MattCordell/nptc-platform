import { useEffect, useRef } from "react";

import { useAuth } from "../auth/session.ts";
import { BackToLandingLink } from "../components/back-to-landing-link.tsx";
import { NoticePage } from "../components/notice-page.tsx";

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
      <NoticePage title="Checking your session" id="register-heading">
        <p className="m-0">One moment.</p>
      </NoticePage>
    );
  }

  if (status === "signed-in") {
    return (
      <NoticePage
        title="You already have an account"
        id="register-heading"
        actions={<BackToLandingLink variant="primary" />}
      >
        <p className="m-0">You are signed in, so there is nothing to register.</p>
      </NoticePage>
    );
  }

  if (status === "unavailable") {
    return (
      <NoticePage
        title="Registration is unavailable"
        id="register-heading"
        actions={<BackToLandingLink variant="primary" />}
      >
        <p className="m-0">
          The platform cannot reach the sign-in service at the moment. Try again in a few
          minutes.
        </p>
      </NoticePage>
    );
  }

  return (
    <NoticePage
      title="Taking you to registration"
      id="register-heading"
      actions={<BackToLandingLink />}
    >
      <p className="m-0">
        You are being sent to the NPTC sign-in service to create an account. If nothing
        happens, your browser may have blocked the redirect - reload this page to try
        again.
      </p>
    </NoticePage>
  );
}
