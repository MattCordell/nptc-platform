import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { FindingIndicator } from "./finding-indicator.tsx";

describe("FindingIndicator", () => {
  it("says 'Open finding' in text when one is open, not by colour alone", () => {
    render(<FindingIndicator />);

    expect(screen.getByText(/Open finding/)).toBeInTheDocument();
  });

  it("hides the decorative mark from assistive technology", () => {
    const { container } = render(<FindingIndicator />);

    expect(container.querySelector("[aria-hidden='true']")?.textContent).toBe("!");
  });

  it("states no detail about the finding (FR-18)", () => {
    const { container } = render(<FindingIndicator />);

    expect(container.textContent).toBe("!Open finding");
  });

  it("uses design tokens only", () => {
    const { container } = render(<FindingIndicator />);

    expectTokenClassesOnly(container.innerHTML.match(/class="[^"]*"/g)?.join(" ") ?? "");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<FindingIndicator />);
    await expectNoA11yViolations(container);
  });
});
