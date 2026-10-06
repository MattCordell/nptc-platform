import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InteractionRequiredError } from "./flow.ts";
import {
  SILENT_RENEW_TIMEOUT_MS,
  silentAuthorize,
  SilentRenewTimeoutError,
} from "./silent-renew.ts";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("silentAuthorize", () => {
  it("times out as a SilentRenewTimeoutError, still an InteractionRequiredError", async () => {
    const outcome = silentAuthorize(
      "http://keycloak.test/auth?prompt=none",
      "http://app.test/auth/callback",
    ).catch((error: unknown) => error);

    await vi.advanceTimersByTimeAsync(SILENT_RENEW_TIMEOUT_MS);

    const error = await outcome;
    expect(error).toBeInstanceOf(SilentRenewTimeoutError);
    expect(error).toBeInstanceOf(InteractionRequiredError);
  });

  it("removes its iframe once it has timed out", async () => {
    const outcome = silentAuthorize(
      "http://keycloak.test/auth?prompt=none",
      "http://app.test/auth/callback",
    ).catch(() => undefined);
    expect(document.querySelectorAll("iframe")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(SILENT_RENEW_TIMEOUT_MS);
    await outcome;

    expect(document.querySelectorAll("iframe")).toHaveLength(0);
  });
});
