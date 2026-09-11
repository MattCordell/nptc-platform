import { describe, expect, it } from "vitest";

import { STATUS_OPTIONS, statusToneFor } from "./status-options.ts";

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

  it("never maps a real STATUS_OPTIONS value onto the unrecognised-status fallback", () => {
    // Catches a lifecycle value added to STATUS_OPTIONS with no matching
    // case in statusToneFor (PR #327 review) - the type union alone can't,
    // since the parameter also accepts a plain string for server data.
    for (const option of STATUS_OPTIONS) {
      expect(statusToneFor(option.value)).not.toBe("neutral");
    }
  });
});
