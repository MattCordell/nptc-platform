import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { CodeChip } from "./code-chip.tsx";

describe("CodeChip", () => {
  // Past Number.MAX_SAFE_INTEGER, with a leading zero on the short one: a
  // coerced value would render differently (FR-06).
  it.each(["999480561000168100", "000123"])("renders %s exactly as given", (code) => {
    render(<CodeChip code={code} />);

    expect(screen.getByText(code).textContent).toBe(code);
  });

  it("is monospaced, left-aligned and never truncated", () => {
    render(<CodeChip code="999480561000168100" />);

    const classes = screen.getByText("999480561000168100").className.split(" ");
    expect(classes).toEqual(
      expect.arrayContaining(["font-mono", "text-left", "whitespace-nowrap"]),
    );
    expect(classes).not.toContain("truncate");
    expect(classes).not.toContain("overflow-hidden");
    expect(classes).not.toContain("text-ellipsis");
  });

  it("takes its colours from the code tokens", () => {
    render(<CodeChip code="000123" />);

    const className = screen.getByText("000123").className;
    expect(className).toContain("bg-[var(--color-code-bg)]");
    expect(className).toContain("text-[var(--color-code-text)]");
    expectTokenClassesOnly(className);
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<CodeChip code="999480561000168100" />);
    await expectNoA11yViolations(container);
  });
});
