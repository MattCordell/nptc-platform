import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useAuth } from "../auth/session.ts";
import { BackToLandingLink } from "../components/back-to-landing-link.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { NoticePage } from "../components/notice-page.tsx";

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

  if (status === "signed-in" || status === "restoring") {
    // "restoring" renders the same waiting screen: telling a user they are
    // signed out before we know would be wrong, and briefly alarming if
    // they then turn out to still have a session.
    return (
      <NoticePage title="Signing you out" id="sign-out-heading">
        <p className="m-0">Ending your session. This should only take a moment.</p>
      </NoticePage>
    );
  }

  return (
    <NoticePage
      title="You are signed out"
      id="sign-out-heading"
      actions={
        <>
          <BackToLandingLink variant="primary" />
          <Link to="/catalogue" className={buttonClassName("secondary")}>
            Search the catalogue
          </Link>
        </>
      }
    >
      <p className="m-0">
        Your session has ended. You can still browse the public catalogue.
      </p>
    </NoticePage>
  );
}
