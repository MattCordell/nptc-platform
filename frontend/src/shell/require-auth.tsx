import { Link, Outlet } from "@tanstack/react-router";

import { useAuthStatus } from "../auth/auth-status.ts";

/**
 * Structural only. This is presentation, not access control: NFR-20 requires
 * every request to be authorised server-side against the internal user
 * record, and no authorisation decision is ever made in the browser. Not
 * rendering a screen here does not protect the data behind it - the API
 * endpoints these screens will call are the actual boundary.
 *
 * Issue #41 replaces the body of `useAuthStatus()` with the real OIDC PKCE
 * session (and adds a `beforeLoad` redirect to `/sign-in?redirect=...`). The
 * route table underneath this layout route does not change - see
 * `require-auth.test.tsx`, which asserts today's placeholder renders at the
 * same pathname a signed-in user's screen will render at later.
 */
export function RequireAuth() {
  const status = useAuthStatus();
  if (status === "signed-in") {
    return <Outlet />;
  }

  return (
    <section aria-labelledby="sign-in-unavailable-heading">
      <h1 id="sign-in-unavailable-heading">Sign-in is not yet available</h1>
      <p>
        This part of the platform needs an account. Sign-in arrives with the Keycloak
        login flow (issue #41); until then, browse the public catalogue.
      </p>
      <Link to="/catalogue">Search the catalogue</Link>
    </section>
  );
}
