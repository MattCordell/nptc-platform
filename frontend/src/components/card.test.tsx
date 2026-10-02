import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { Card } from "./card.tsx";

describe("Card", () => {
  it("renders its children", () => {
    render(<Card>Card body</Card>);
    expect(screen.getByText("Card body")).toBeInTheDocument();
  });

  // Class-contract assertion, not rendered colour: jsdom cannot resolve a
  // `var(--*)` to a computed value.
  it("takes its border, radius and fill from tokens, with 24px padding", () => {
    const { container } = render(<Card>Card body</Card>);

    const classes = (container.firstElementChild as HTMLElement).className.split(" ");
    expect(classes).toEqual(
      expect.arrayContaining([
        "border",
        "border-[var(--color-border)]",
        "bg-[var(--color-surface)]",
        "rounded-[var(--radius-card)]",
        "p-6",
      ]),
    );
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(<Card>Card body</Card>);

    expectTokenClassesOnly((container.firstElementChild as HTMLElement).className);
  });

  it("passes native div props and a caller className through", () => {
    render(
      <Card role="region" aria-label="Summary" className="extra">
        Card body
      </Card>,
    );

    const card = screen.getByRole("region", { name: "Summary" });
    expect(card.className).toContain("extra");
    expect(card.className).toContain("p-6");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <Card role="region" aria-label="Summary">
        <h2>Heading</h2>
        <p>Body</p>
      </Card>,
    );
    await expectNoA11yViolations(container);
  });
});
