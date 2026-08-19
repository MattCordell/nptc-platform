/**
 * The in-flight login transaction, held across the redirect to Keycloak and
 * back (issue #41).
 *
 * `sessionStorage`, not `localStorage`: the transaction is meaningful only
 * to the tab that started it, and it must not outlive the browser session.
 * Nothing stored here is a credential - a `code_verifier` is worthless
 * without the matching authorisation code, which never touches storage.
 *
 * **Single use is the security property.** `takeTransaction` reads and
 * deletes in one step, so a callback URL that is replayed (refreshed,
 * bookmarked, pasted into another tab, or fed to the user by an attacker)
 * finds nothing and is refused. Leaving the entry in place would make a
 * replayed callback indistinguishable from a genuine one.
 */

const STORAGE_KEY = "nptc.auth.transaction";

export interface LoginTransaction {
  state: string;
  codeVerifier: string;
  nonce: string;
  /** Where to send the user once the exchange completes. */
  redirect?: string;
}

function storage(): Storage | null {
  // Private-browsing modes and hardened configurations can make
  // sessionStorage throw on access rather than merely be absent, so this is
  // a try/catch rather than a truthiness check.
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function saveTransaction(transaction: LoginTransaction): void {
  storage()?.setItem(STORAGE_KEY, JSON.stringify(transaction));
}

/**
 * Returns the stored transaction and removes it, or `null` if there is
 * none (or it is unreadable). The delete happens even when the value fails
 * to parse: a corrupt entry must not be retryable either.
 */
export function takeTransaction(): LoginTransaction | null {
  const store = storage();
  if (!store) {
    return null;
  }
  const raw = store.getItem(STORAGE_KEY);
  store.removeItem(STORAGE_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as LoginTransaction).state === "string" &&
      typeof (parsed as LoginTransaction).codeVerifier === "string"
    ) {
      return parsed as LoginTransaction;
    }
    return null;
  } catch {
    return null;
  }
}

/** Used when abandoning a flow, so a stale transaction cannot be resumed. */
export function clearTransaction(): void {
  storage()?.removeItem(STORAGE_KEY);
}
