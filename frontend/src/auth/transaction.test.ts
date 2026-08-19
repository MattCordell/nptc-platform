import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearTransactions, saveTransaction, takeTransaction } from "./transaction.ts";

/**
 * The in-flight login transactions (issue #41).
 *
 * Two properties under test, both security-relevant:
 *
 * - **Single use** — what makes a replayed callback URL fail, without a
 *   network call and without relying on Keycloak to notice.
 * - **Keyed by `state`** — what lets a silent renewal and an interactive
 *   sign-in be in flight at once without clobbering each other. With one
 *   shared slot the loser of that race fails a sign-in that actually
 *   worked.
 */

function transaction(state: string, redirect?: string) {
  return { state, codeVerifier: `verifier-${state}`, nonce: `nonce-${state}`, redirect };
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.useRealTimers();
});

describe("takeTransaction", () => {
  it("returns what was saved under that state", () => {
    saveTransaction(transaction("abc", "/submissions"));

    expect(takeTransaction("abc")).toEqual(transaction("abc", "/submissions"));
  });

  it("consumes it, so a second read finds nothing", () => {
    saveTransaction(transaction("abc"));

    expect(takeTransaction("abc")).not.toBeNull();
    expect(takeTransaction("abc")).toBeNull();
  });

  it("returns null for a state that was never issued", () => {
    saveTransaction(transaction("abc"));

    // The lookup is the state check: a forged callback simply finds nothing.
    expect(takeTransaction("not-issued")).toBeNull();
    // ...and must not have consumed the genuine one on its way past.
    expect(takeTransaction("abc")).not.toBeNull();
  });

  it("returns null when no sign-in was started at all", () => {
    expect(takeTransaction("abc")).toBeNull();
  });

  it("discards a corrupt entry rather than leaving it retryable", () => {
    window.sessionStorage.setItem("nptc.auth.transaction.abc", "{not json");

    expect(takeTransaction("abc")).toBeNull();
    expect(takeTransaction("abc")).toBeNull();
  });

  it("rejects a well-formed entry missing what the flow needs", () => {
    window.sessionStorage.setItem(
      "nptc.auth.transaction.abc",
      JSON.stringify({ redirect: "/somewhere", createdAt: Date.now() }),
    );

    expect(takeTransaction("abc")).toBeNull();
  });
});

describe("concurrent flows", () => {
  it("keeps a silent renewal and an interactive sign-in apart", () => {
    // The race the single-slot version lost: whichever saved second used
    // to overwrite the other, and the surviving callback failed its state
    // check on a sign-in that had worked.
    saveTransaction(transaction("interactive", "/submissions"));
    saveTransaction(transaction("renewal"));

    expect(takeTransaction("interactive")?.redirect).toBe("/submissions");
    expect(takeTransaction("renewal")?.codeVerifier).toBe("verifier-renewal");
  });
});

describe("expiry and cleanup", () => {
  it("abandons a transaction left far too long", () => {
    vi.useFakeTimers();
    saveTransaction(transaction("stale"));

    vi.advanceTimersByTime(16 * 60 * 1000);

    expect(takeTransaction("stale")).toBeNull();
  });

  it("sweeps abandoned transactions when a new flow starts", () => {
    vi.useFakeTimers();
    saveTransaction(transaction("stale"));

    vi.advanceTimersByTime(16 * 60 * 1000);
    saveTransaction(transaction("fresh"));

    // Swept on save, so an abandoned tab does not accumulate entries.
    expect(window.sessionStorage.getItem("nptc.auth.transaction.stale")).toBeNull();
    expect(takeTransaction("fresh")).not.toBeNull();
  });
});

describe("storage discipline", () => {
  it("keeps nothing in localStorage", () => {
    saveTransaction(transaction("abc"));

    // ADR-0021: nothing about a session may outlive the browser session.
    expect(window.localStorage.length).toBe(0);
  });

  it("clearTransactions abandons every in-flight sign-in", () => {
    saveTransaction(transaction("one"));
    saveTransaction(transaction("two"));

    clearTransactions();

    expect(takeTransaction("one")).toBeNull();
    expect(takeTransaction("two")).toBeNull();
  });
});
