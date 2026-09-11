import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { StatusBadge } from "./status-badge.tsx";

describe("StatusBadge", () => {
  it.each([
    ["draft", "--color-status-draft-text", "--color-status-draft-bg"],
    ["active", "--color-status-active-text", "--color-status-active-bg"],
    ["deprecated", "--color-status-deprecated-text", "--color-status-deprecated-bg"],
    ["neutral", "--color-status-neutral-text", "--color-status-neutral-bg"],
  ] as const)("renders the %s tone's colour pair", (tone, textVar, bgVar) => {
    render(<StatusBadge tone={tone} label="Some status" />);

    const badge = screen.getByText("Some status");
    expect(badge.className).toContain(`text-[var(${textVar})]`);
    expect(badge.className).toContain(`bg-[var(${bgVar})]`);
  });

  it("renders the given label", () => {
    render(<StatusBadge tone="draft" label="Draft" />);
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it.each(["draft", "active", "deprecated", "neutral"] as const)(
    "has no automated accessibility violations for the %s tone",
    async (tone) => {
      const { container } = render(<StatusBadge tone={tone} label="Some status" />);
      await expectNoA11yViolations(container);
    },
  );
});
