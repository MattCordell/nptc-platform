import { describe, expect, it } from "vitest";

import { statusToneFor } from "./status-options.ts";

describe("statusToneFor", () => {
  it.each([
    ["draft", "draft"],
    ["active", "active"],
    ["deprecated", "deprecated"],
    ["withdrawn", "deprecated"],
  ] as const)("maps %s to the %s tone", (status, tone) => {
    expect(statusToneFor(status)).toBe(tone);
  });

  it("falls back to neutral for an unrecognised status", () => {
    expect(statusToneFor("archived")).toBe("neutral");
  });
});
