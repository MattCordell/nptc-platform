import { describe, expect, it } from "vitest";

import { quotaMessage, waitPhrase } from "./quota-message.ts";

describe("waitPhrase", () => {
  it.each([
    [1, "in 1 second"],
    [45, "in 45 seconds"],
    [60, "in about 1 minute"],
    [61, "in about 2 minutes"],
    [1800, "in about 30 minutes"],
    [3600, "in about 60 minutes"],
    [3601, "in about 2 hours"],
  ])("says %i seconds as %s, never earlier than the wait", (seconds, expected) => {
    expect(waitPhrase(seconds)).toBe(expected);
  });
});

describe("quotaMessage", () => {
  it("says when an hourly limit lifts, and falls back when the header is missing", () => {
    const body = { detail: "x", limit: "hourly", maximum: 20 } as const;

    expect(quotaMessage(body, 120)).toContain("You can submit again in about 2 minutes.");
    expect(quotaMessage(body, null)).toContain("You can submit again later.");
  });

  // The principal failure mode: promising that waiting helps would loop the
  // user through a refusal that never lifts.
  it("never says a lifetime limit lifts, even when a wait is given", () => {
    const message = quotaMessage({ detail: "x", limit: "lifetime", maximum: 5 }, 600);

    expect(message).toContain("Waiting will not lift this limit.");
    expect(message).not.toMatch(/submit again|in about/);
  });

  it("asks the user to retry shortly for a concurrent refusal", () => {
    const body = { detail: "x", limit: "concurrent", maximum: 1 } as const;

    expect(quotaMessage(body, 5)).toContain("Try again in 5 seconds.");
    expect(quotaMessage(body, null)).toContain("Try again in a few seconds.");
  });
});
