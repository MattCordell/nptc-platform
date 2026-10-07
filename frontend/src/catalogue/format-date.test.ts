import { describe, expect, it } from "vitest";

import { formatDate } from "./format-date.ts";

describe("formatDate", () => {
  // Midday UTC is the same calendar day in every time zone from UTC-11 to
  // UTC+11, so this holds wherever the test runs.
  it("writes the day, month name and year", () => {
    expect(formatDate("2026-09-01T12:00:00Z")).toBe("1 September 2026");
  });

  it("returns an unparseable value as given", () => {
    expect(formatDate("not a date")).toBe("not a date");
  });
});
