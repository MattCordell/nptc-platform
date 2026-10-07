import { describe, expect, it } from "vitest";

import { asTermsRequired, asTermsVersionStale } from "./terms.ts";
import { ApiError } from "./unwrap.ts";

/**
 * NFR-45 / NFR-47: the SPA routes on `code`, never on `detail`. The principal
 * failure mode is a refusal with the same status but a different meaning - an
 * ordinary 403 for a missing permission, and the other 409 on the same route.
 */

const REQUIRED = {
  detail: "You must accept the current terms of use before you can do this.",
  code: "terms_acceptance_required",
};

const STALE = {
  detail: "The terms have changed since you read them.",
  code: "terms_version_stale",
  current_version: "2026-11-01",
};

describe("asTermsRequired", () => {
  it("narrows a 403 carrying the terms code", () => {
    expect(asTermsRequired(new ApiError(403, REQUIRED))?.code).toBe(
      "terms_acceptance_required",
    );
  });

  it("returns null for a 403 without the code, such as a missing permission", () => {
    expect(asTermsRequired(new ApiError(403, { detail: "Forbidden." }))).toBeNull();
  });

  it("ignores the wording of detail", () => {
    expect(
      asTermsRequired(
        new ApiError(403, { ...REQUIRED, detail: "A sentence the server reworded." }),
      ),
    ).not.toBeNull();
    expect(asTermsRequired(new ApiError(403, { detail: REQUIRED.detail }))).toBeNull();
  });

  it("returns null when the code arrives on another status", () => {
    expect(asTermsRequired(new ApiError(409, REQUIRED))).toBeNull();
  });

  it("returns null for a non-ApiError and for a body that is not an object", () => {
    expect(asTermsRequired(new Error("boom"))).toBeNull();
    expect(asTermsRequired(new ApiError(403, "text"))).toBeNull();
    expect(asTermsRequired(new ApiError(403, null))).toBeNull();
  });
});

describe("asTermsVersionStale", () => {
  it("narrows a 409 carrying the code and keeps current_version", () => {
    expect(asTermsVersionStale(new ApiError(409, STALE))?.current_version).toBe(
      "2026-11-01",
    );
  });

  it("returns null for the other 409 on this route, which has no code", () => {
    expect(
      asTermsVersionStale(
        new ApiError(409, { detail: "More than one account matches your sign-in." }),
      ),
    ).toBeNull();
  });

  it("returns null when current_version is missing", () => {
    expect(
      asTermsVersionStale(
        new ApiError(409, { detail: STALE.detail, code: "terms_version_stale" }),
      ),
    ).toBeNull();
  });

  it("returns null when the code arrives on another status", () => {
    expect(asTermsVersionStale(new ApiError(403, STALE))).toBeNull();
  });

  it("returns null for a non-ApiError", () => {
    expect(asTermsVersionStale(new Error("boom"))).toBeNull();
  });
});
