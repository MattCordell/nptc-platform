import { Link, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useAuthStatus } from "../auth/auth-status.ts";

/**
 * Structural only. This is presentation, not access control: NFR-20 requires
 * every request to be authorised server-side against the internal user
 * record, and no authorisation decision is ever made in the browser. Not
 * rendering a screen here does not protect the data behind it - the API
 * endpoints these screens will call are the actual boundary.
 *
 * A signed-out visitor is sent to `/sign-in?redirect=...` so they land back
 * where they were aiming once they have signed in. The redirect runs from an
 * effect rather than a route `beforeLoad`: on a cold load the session is not
 * known until `AuthProvider`'s silent renewal has resolved, and a
 * `beforeLoad` guard would bounce every already-signed-in user to the
 * sign-in page before their session had a chance to restore.
 */
export function RequireAuth() {
  const status = useAuthStatus();
  const navigate = useNavigate();
  const { href } = useLocation();
  // Fired at most once per mount, and deliberately *not* re-run when `href`
  // changes. Without this the effect re-runs as the navigation it just
  // started lands, reads the new `/sign-in?redirect=...` as the place to
  // come back to, and redirects to itself - nesting one encoded copy of the
  // URL inside the next until the browser gives up.
  const redirected = useRef(false);
  const target = useRef(href);

  useEffect(() => {
    if (status !== "signed-out" || redirected.current) {
      return;
    }
    redirected.current = true;
    // `replace`, so the protected URL does not sit in history between the
    // two entries and send the user straight back here on "back".
    void navigate({
      to: "/sign-in",
      search: { redirect: target.current },
      replace: true,
    });
  }, [status, navigate]);

  if (status === "signed-in") {
    return <Outlet />;
  }

  if (status === "restoring") {
    // Deliberately not a redirect. On a cold deep-link the session is
    // unknown for one silent round trip, and treating that as signed-out
    // would send a user with a perfectly good SSO session out of the SPA.
    return (
      <section aria-labelledby="sign-in-required-heading">
        <h1 id="sign-in-required-heading">Checking your session</h1>
        <p>One moment.</p>
      </section>
    );
  }

  if (status === "signed-out") {
    return (
      <section aria-labelledby="sign-in-required-heading">
        <h1 id="sign-in-required-heading">Taking you to sign in</h1>
        <p>This part of the platform needs an account.</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="sign-in-unavailable-heading">
      <h1 id="sign-in-unavailable-heading">Sign-in is unavailable</h1>
      <p>
        The platform cannot reach the sign-in service at the moment, so this screen cannot
        be shown. Try again in a few minutes; the public catalogue is still available
        meanwhile.
      </p>
      <Link to="/catalogue">Search the catalogue</Link>
    </section>
  );
}
