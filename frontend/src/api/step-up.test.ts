import { describe, expect, it } from "vitest";

import { asStepUpChallenge, parseStepUpChallenge } from "./step-up.ts";
import { ApiError } from "./unwrap.ts";

const CHALLENGE = 'Bearer error="insufficient_user_authentication", acr_values="2"';

describe("parseStepUpChallenge", () => {
  it("reads acr_values from a step-up challenge", () => {
    expect(parseStepUpChallenge(CHALLENGE)).toEqual({ acrValues: "2" });
  });

  // The realm's LoA map can configure more than one satisfying value -
  // this must not be hard-coded to a single digit.
  it("reads a multi-value acr_values unchanged", () => {
    expect(
      parseStepUpChallenge(
        'Bearer error="insufficient_user_authentication", acr_values="2 3"',
      ),
    ).toEqual({ acrValues: "2 3" });
  });

  it("returns null for a plain Bearer 401 challenge", () => {
    expect(parseStepUpChallenge("Bearer")).toBeNull();
  });

  it("returns null for a different error discriminator", () => {
    expect(
      parseStepUpChallenge('Bearer error="insufficient_scope", scope="admin"'),
    ).toBeNull();
  });

  it("returns null when acr_values is missing", () => {
    expect(
      parseStepUpChallenge('Bearer error="insufficient_user_authentication"'),
    ).toBeNull();
  });

  it("returns null for no header at all", () => {
    expect(parseStepUpChallenge(null)).toBeNull();
  });
});

describe("asStepUpChallenge", () => {
  it("recognises a step-up challenge on a 403 ApiError", () => {
    const headers = new Headers({ "WWW-Authenticate": CHALLENGE });
    const error = new ApiError(403, { detail: "MFA required" }, headers);

    expect(asStepUpChallenge(error)).toEqual({ acrValues: "2" });
  });

  // FR-44's negative case: an ordinary missing-permission 403 carries no
  // challenge header at all, and must not be sent through step-up.
  it("returns null for a plain 403 with no challenge header", () => {
    const error = new ApiError(403, { detail: "You do not have permission." });

    expect(asStepUpChallenge(error)).toBeNull();
  });

  it("returns null for a non-403 status, even with the header present", () => {
    const headers = new Headers({ "WWW-Authenticate": CHALLENGE });
    const error = new ApiError(401, { detail: "not authenticated" }, headers);

    expect(asStepUpChallenge(error)).toBeNull();
  });

  it("returns null for a non-ApiError", () => {
    expect(asStepUpChallenge(new Error("network down"))).toBeNull();
  });
});
