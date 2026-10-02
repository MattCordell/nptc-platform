import { describe, expect, it } from "vitest";

import { expectTokenClassesOnly } from "./token-classes.ts";

describe("expectTokenClassesOnly", () => {
  it("accepts token-based arbitrary-value classes", () => {
    expect(() =>
      expectTokenClassesOnly(
        "border-[var(--color-border)] bg-[var(--color-surface)] rounded-[var(--radius-card)] p-6",
      ),
    ).not.toThrow();
  });

  it.each([
    ["a hex value", "bg-[#ffffff] p-6"],
    ["a palette class", "bg-gray-100 p-6"],
    ["a palette text class", "text-red-500"],
    ["a shadow class", "shadow-md p-6"],
    ["an arbitrary shadow class", "shadow-[0_1px_2px_black]"],
  ])("rejects %s", (_label, className) => {
    expect(() => expectTokenClassesOnly(className)).toThrow();
  });
});
