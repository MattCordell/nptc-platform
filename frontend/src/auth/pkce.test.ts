import { describe, expect, it } from "vitest";

import {
  createCodeChallenge,
  createCodeVerifier,
  createNonce,
  createState,
} from "./pkce.ts";

/**
 * PKCE primitives (issue #41, NFR-01).
 *
 * The challenge is checked against RFC 7636's own worked example rather
 * than against this module's output. A round-trip test
 * (`challenge(verifier)` equals itself) would pass just as happily with the
 * padding left on or the wrong alphabet - and Keycloak compares the string
 * exactly, so either mistake fails the exchange with nothing but
 * `invalid_grant` to go on.
 */

// RFC 7636 Appendix B.
const RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
const RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

describe("createCodeChallenge", () => {
  it("matches RFC 7636's worked example", async () => {
    expect(await createCodeChallenge(RFC_VERIFIER)).toBe(RFC_CHALLENGE);
  });

  it("is base64url with no padding", async () => {
    const challenge = await createCodeChallenge(createCodeVerifier());

    expect(challenge).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(challenge).not.toContain("=");
    expect(challenge).not.toContain("+");
    expect(challenge).not.toContain("/");
  });
});

describe("random values", () => {
  it("produces verifiers inside RFC 7636's length range", () => {
    const verifier = createCodeVerifier();

    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(verifier.length).toBeLessThanOrEqual(128);
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("never repeats a verifier, state or nonce", () => {
    // A repeated `state` would make a forged callback indistinguishable
    // from a genuine one, so uniqueness is the security property, not a
    // nicety.
    const verifiers = new Set(Array.from({ length: 50 }, () => createCodeVerifier()));
    const states = new Set(Array.from({ length: 50 }, () => createState()));
    const nonces = new Set(Array.from({ length: 50 }, () => createNonce()));

    expect(verifiers.size).toBe(50);
    expect(states.size).toBe(50);
    expect(nonces.size).toBe(50);
  });
});
