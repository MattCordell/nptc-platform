import { useEffect, useRef } from "react";

import { useAuth } from "./session.ts";

/**
 * Attempts one silent session restore on a cold load (issue #41).
 *
 * ADR-0021 keeps tokens in memory only, so a page reload starts with no
 * session even though the Keycloak SSO cookie may still be perfectly valid.
 * Without this, every refresh would look like a sign-out. The restore is a
 * `prompt=none` authorize in a hidden iframe: it either produces a session
 * or resolves quietly as signed-out, and never shows the user anything.
 *
 * A component rather than an effect inside `AuthProvider` so that the
 * provider itself stays free of mount side effects, and so a test can mount
 * the provider without triggering a network call it did not ask for.
 */
export function SessionRestore() {
  const { restore } = useAuth();
  // StrictMode double-invokes mount effects; one restore attempt is enough
  // and two would race for the same transaction slot.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;
    void restore();
  }, [restore]);

  return null;
}
