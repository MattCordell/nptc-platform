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
    ["a variant-prefixed palette class", "hover:bg-gray-100"],
    ["a palette class with an opacity", "bg-white/50"],
    ["a directional border colour", "border-t-red-500"],
    ["a divide colour", "divide-gray-200"],
    ["a gradient from colour", "from-blue-500"],
    ["a gradient via colour", "via-blue-500"],
    ["a gradient to colour", "to-blue-500"],
    ["a placeholder colour", "placeholder-gray-400"],
    ["an accent colour", "accent-teal-600"],
    ["a caret colour", "caret-red-500"],
    ["a decoration colour", "decoration-blue-500"],
    ["a ring offset colour", "ring-offset-gray-100"],
    ["an inset ring colour", "inset-ring-gray-200"],
    ["an rgb() value", "bg-[rgb(0,0,0)]"],
    ["an hsl() value", "text-[hsl(10,20%,30%)]"],
    ["an oklch() value", "border-[oklch(0.5_0.1_200)]"],
  ])("rejects %s", (_label, className) => {
    expect(() => expectTokenClassesOnly(className)).toThrow();
  });
});
