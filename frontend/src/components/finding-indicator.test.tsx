import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { FindingIndicator } from "./finding-indicator.tsx";

describe("FindingIndicator", () => {
  it("says 'Open finding' in text when one is open, not by colour alone", () => {
    render(<FindingIndicator open />);

    expect(screen.getByText(/Open finding/)).toBeInTheDocument();
  });

  it("hides the decorative mark from assistive technology", () => {
    const { container } = render(<FindingIndicator open />);

    expect(container.querySelector("[aria-hidden='true']")?.textContent).toBe("!");
  });

  it("says 'None' when no finding is open", () => {
    render(<FindingIndicator open={false} />);

    expect(screen.getByText("None")).toBeInTheDocument();
    expect(screen.queryByText(/Open finding/)).toBeNull();
  });

  it("states no detail about the finding (FR-18)", () => {
    const { container } = render(<FindingIndicator open />);

    expect(container.textContent).toBe("!Open finding");
  });

  it.each([true, false])("uses design tokens only when open is %s", (open) => {
    const { container } = render(<FindingIndicator open={open} />);

    expectTokenClassesOnly(container.innerHTML.match(/class="[^"]*"/g)?.join(" ") ?? "");
  });

  it.each([true, false])(
    "has no automated accessibility violations when open is %s",
    async (open) => {
      const { container } = render(<FindingIndicator open={open} />);
      await expectNoA11yViolations(container);
    },
  );
});
