import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { StatusBadge } from "./status-badge.tsx";

describe("StatusBadge", () => {
  // Asserts the Tailwind class contract, not rendered colour - jsdom cannot
  // resolve a `var(--color-*)` to a computed colour (PR #327 review), so
  // this is a change-detector for TONE_CLASSES, not proof a tone renders
  // the right colour. frontend/tests/design-tokens-contrast.test.ts checks
  // the token values themselves (contrast); nothing here checks that this
  // component wires a tone to the *right* pair beyond the string match.
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

  // Kept for consistency with every other component's co-located a11y sweep
  // (docs/architecture/components.md), but on a bare <span> with axe's
  // color-contrast rule disabled under jsdom, no rule here can actually
  // fire (PR #327 review) - it is harmless, not evidence the tones are
  // contrast-safe. frontend/tests/design-tokens-contrast.test.ts is what
  // checks that.
  it.each(["draft", "active", "deprecated", "neutral"] as const)(
    "has no automated accessibility violations for the %s tone",
    async (tone) => {
      const { container } = render(<StatusBadge tone={tone} label="Some status" />);
      await expectNoA11yViolations(container);
    },
  );
});
