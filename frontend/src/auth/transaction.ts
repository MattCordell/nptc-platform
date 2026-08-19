/**
 * The in-flight login transactions, held across the redirect to Keycloak
 * and back (issue #41).
 *
 * `sessionStorage`, not `localStorage`: a transaction is meaningful only to
 * the tab that started it, and it must not outlive the browser session.
 * Nothing stored here is a credential - a `code_verifier` is worthless
 * without the matching authorisation code, which never touches storage.
 *
 * **Keyed by `state`, not a single slot.** More than one flow can genuinely
 * be in flight at once: a silent `prompt=none` renewal runs alongside an
 * interactive sign-in, and both go through `buildAuthorizeUrl`. With one
 * shared slot the second to start would overwrite the first, and whichever
 * callback arrived would find a transaction whose `state` did not match -
 * failing a sign-in that actually worked, with the outcome decided by
 * whichever fetch resolved first.
 *
 * **Single use is the security property.** `takeTransaction` reads and
 * deletes in one step, so a callback URL that is replayed (refreshed,
 * bookmarked, pasted into another tab, or fed to the user by an attacker)
 * finds nothing and is refused. And because the key *is* the `state`, a
 * callback bearing a `state` we never issued simply finds no transaction -
 * the mismatch check and the lookup are the same operation.
 */

const KEY_PREFIX = "nptc.auth.transaction.";

/**
 * A login round trip should take seconds. Anything older than this is
 * abandoned - a tab left open on a half-finished sign-in should not leave
 * a usable transaction behind indefinitely.
 */
const MAX_AGE_MS = 15 * 60 * 1000;

export interface LoginTransaction {
  state: string;
  codeVerifier: string;
  nonce: string;
  /** Where to send the user once the exchange completes. */
  redirect?: string;
}

interface StoredTransaction extends LoginTransaction {
  createdAt: number;
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

function parse(raw: string | null): StoredTransaction | null {
  if (!raw) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as StoredTransaction).state === "string" &&
      typeof (parsed as StoredTransaction).codeVerifier === "string"
    ) {
      return parsed as StoredTransaction;
    }
    return null;
  } catch {
    return null;
  }
}

function keys(store: Storage): string[] {
  const found: string[] = [];
  for (let index = 0; index < store.length; index += 1) {
    const key = store.key(index);
    if (key?.startsWith(KEY_PREFIX)) {
      found.push(key);
    }
  }
  return found;
}

/**
 * Drops abandoned and unreadable transactions. Called on save, so an
 * abandoned flow is cleaned up by the next one rather than accumulating.
 */
function sweep(store: Storage, now: number): void {
  for (const key of keys(store)) {
    const stored = parse(store.getItem(key));
    if (!stored || now - stored.createdAt > MAX_AGE_MS) {
      store.removeItem(key);
    }
  }
}

export function saveTransaction(transaction: LoginTransaction): void {
  const store = storage();
  if (!store) {
    return;
  }
  const now = Date.now();
  sweep(store, now);
  const stored: StoredTransaction = { ...transaction, createdAt: now };
  store.setItem(`${KEY_PREFIX}${transaction.state}`, JSON.stringify(stored));
}

/**
 * Returns the transaction issued for `state` and removes it, or `null` if
 * there is none - which covers a replay, a `state` we never issued, and an
 * expired flow alike. All three are the same refusal.
 */
export function takeTransaction(state: string): LoginTransaction | null {
  const store = storage();
  if (!store) {
    return null;
  }
  const key = `${KEY_PREFIX}${state}`;
  const stored = parse(store.getItem(key));
  // Removed even when it failed to parse: a corrupt entry must not be
  // retryable either.
  store.removeItem(key);
  if (!stored || Date.now() - stored.createdAt > MAX_AGE_MS) {
    return null;
  }
  return {
    state: stored.state,
    codeVerifier: stored.codeVerifier,
    nonce: stored.nonce,
    redirect: stored.redirect,
  };
}

/** Abandons every in-flight sign-in in this tab (used on sign-out). */
export function clearTransactions(): void {
  const store = storage();
  if (!store) {
    return;
  }
  for (const key of keys(store)) {
    store.removeItem(key);
  }
}
