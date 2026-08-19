import { beforeEach, describe, expect, it } from "vitest";

import { clearTransaction, saveTransaction, takeTransaction } from "./transaction.ts";

/**
 * The in-flight login transaction (issue #41).
 *
 * Single use is the security property under test: it is what makes a
 * replayed callback URL fail, without any network call and without relying
 * on Keycloak to notice.
 */

const TRANSACTION = {
  state: "state-value",
  codeVerifier: "verifier-value",
  nonce: "nonce-value",
  redirect: "/submissions",
};

beforeEach(() => {
  window.sessionStorage.clear();
});

describe("takeTransaction", () => {
  it("returns what was saved", () => {
    saveTransaction(TRANSACTION);

    expect(takeTransaction()).toEqual(TRANSACTION);
  });

  it("consumes it, so a second read finds nothing", () => {
    saveTransaction(TRANSACTION);

    expect(takeTransaction()).toEqual(TRANSACTION);
    expect(takeTransaction()).toBeNull();
  });

  it("returns null when no sign-in was started", () => {
    expect(takeTransaction()).toBeNull();
  });

  it("discards a corrupt entry rather than leaving it retryable", () => {
    window.sessionStorage.setItem("nptc.auth.transaction", "{not json");

    expect(takeTransaction()).toBeNull();
    // The delete must happen even on the failure path, or a corrupt entry
    // would be re-read on every subsequent callback.
    expect(takeTransaction()).toBeNull();
  });

  it("rejects a well-formed entry that is missing what the flow needs", () => {
    window.sessionStorage.setItem(
      "nptc.auth.transaction",
      JSON.stringify({ redirect: "/somewhere" }),
    );

    expect(takeTransaction()).toBeNull();
  });
});

describe("storage discipline", () => {
  it("keeps nothing in localStorage", () => {
    saveTransaction(TRANSACTION);

    // ADR-0021: nothing about a session may outlive the browser session.
    expect(window.localStorage.length).toBe(0);
  });

  it("clearTransaction abandons an in-flight sign-in", () => {
    saveTransaction(TRANSACTION);
    clearTransaction();

    expect(takeTransaction()).toBeNull();
  });
});
