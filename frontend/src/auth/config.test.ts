import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLBACK_PATH, readAuthConfig } from "./config.ts";

/**
 * Reading the sign-in configuration (issue #41).
 *
 * The failing case matters more than the happy one: a missing issuer must
 * name the variable and stop, never quietly fall back to a plausible
 * localhost URL. A defaulted issuer would send users to an authorisation
 * server nobody chose to trust - and it would do so silently, in exactly
 * the deployment where someone forgot to set it.
 */

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("readAuthConfig", () => {
  it("derives the redirect URIs from the current origin", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.test/realms/nptc");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "nptc-frontend");

    const config = readAuthConfig("https://app.test");

    expect(config.redirectUri).toBe(`https://app.test${CALLBACK_PATH}`);
    expect(config.postLogoutRedirectUri).toBe("https://app.test");
  });

  it("strips a trailing slash from the issuer", () => {
    // `${issuer}/.well-known/...` would otherwise contain `//`, which some
    // reverse proxies do not normalise.
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.test/realms/nptc/");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "nptc-frontend");

    expect(readAuthConfig("https://app.test").issuer).toBe(
      "https://idp.test/realms/nptc",
    );
  });

  it("throws, naming the variable, when the issuer is missing", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "nptc-frontend");

    expect(() => readAuthConfig("https://app.test")).toThrow(/VITE_OIDC_ISSUER/);
  });

  it("throws, naming the variable, when the client id is missing", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://idp.test/realms/nptc");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "");

    expect(() => readAuthConfig("https://app.test")).toThrow(/VITE_OIDC_CLIENT_ID/);
  });

  it("treats a whitespace-only value as missing", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "   ");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "nptc-frontend");

    expect(() => readAuthConfig("https://app.test")).toThrow(/VITE_OIDC_ISSUER/);
  });
});
