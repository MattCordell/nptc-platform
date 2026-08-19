/**
 * The hidden-iframe `prompt=none` renewal (issue #41, ADR-0021).
 *
 * ADR-0021 holds tokens in memory only and asks for no refresh token, so
 * this is how a session survives an expiring access token and a page
 * reload: a `prompt=none` authorization request against the Keycloak SSO
 * cookie, which either answers with a code or with `login_required`.
 *
 * An iframe rather than a top-level redirect, because the whole point is
 * that the user does not lose their place.
 *
 * Kept in its own module so `AuthProvider` can be handed a substitute in
 * tests: jsdom does not perform real navigation, so the real one can only
 * ever time out there, and a ten-second timeout per test is not a test
 * suite anybody runs.
 */

import { InteractionRequiredError } from "./flow.ts";

/** The renewal must not hang the UI if Keycloak is slow or unreachable. */
export const SILENT_RENEW_TIMEOUT_MS = 10_000;

export type SilentAuthorize = (
  url: string,
  redirectUri: string,
) => Promise<URLSearchParams>;

export const silentAuthorize: SilentAuthorize = (url, redirectUri) =>
  new Promise((resolve, reject) => {
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    frame.setAttribute("title", "silent sign-in renewal");

    let settled = false;
    const finish = (outcome: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      frame.remove();
      outcome();
    };

    // A timeout, not an error state: the commonest reason the iframe never
    // reaches our callback is that Keycloak wants interaction, which is an
    // ordinary "you are signed out", not a fault.
    const timer = window.setTimeout(() => {
      finish(() => {
        reject(new InteractionRequiredError("silent renewal timed out"));
      });
    }, SILENT_RENEW_TIMEOUT_MS);

    frame.addEventListener("load", () => {
      let search: string;
      try {
        // While the iframe is still on Keycloak's origin this read throws,
        // and we simply wait for the next load or the timeout. Once it has
        // been redirected to our own callback it is same-origin and
        // readable - which is why the callback path must be ours.
        const location = frame.contentWindow?.location;
        if (!location) {
          return;
        }
        // Compared as origin + pathname, not `startsWith`: a prefix match
        // would also accept `/auth/callbackanything`. The same-origin
        // `try`/`catch` carries the real weight here, but an exact compare
        // is no longer to write.
        const expected = new URL(redirectUri);
        const actual = new URL(location.href);
        if (actual.origin !== expected.origin || actual.pathname !== expected.pathname) {
          return;
        }
        search = location.search;
      } catch {
        return;
      }
      finish(() => {
        resolve(new URLSearchParams(search));
      });
    });

    frame.src = url;
    document.body.appendChild(frame);
  });
