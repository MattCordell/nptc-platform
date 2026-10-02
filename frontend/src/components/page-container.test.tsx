import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { PageContainer } from "./page-container.tsx";

describe("PageContainer", () => {
  it("renders its children", () => {
    render(
      <PageContainer>
        <p>First</p>
        <p>Second</p>
      </PageContainer>,
    );

    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
  });

  // Class-contract assertion, not rendered layout: jsdom does not resolve
  // Tailwind utilities or `var(--*)` to computed values.
  it("centres at the page-width token with a fixed gutter and rhythm", () => {
    const { container } = render(<PageContainer>Content</PageContainer>);

    const classes = (container.firstElementChild as HTMLElement).className.split(" ");
    expect(classes).toEqual(
      expect.arrayContaining([
        "mx-auto",
        "max-w-page",
        "px-6",
        "flex",
        "flex-col",
        "gap-6",
      ]),
    );
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(<PageContainer>Content</PageContainer>);

    expectTokenClassesOnly((container.firstElementChild as HTMLElement).className);
  });

  it("renders a plain div: no landmark role", () => {
    const { container } = render(<PageContainer>Content</PageContainer>);

    expect(container.firstElementChild?.tagName).toBe("DIV");
  });

  it("passes native div props and a caller className through", () => {
    render(
      <PageContainer data-testid="wrapper" className="extra" aria-label="Region">
        Content
      </PageContainer>,
    );

    const wrapper = screen.getByTestId("wrapper");
    expect(wrapper).toHaveAttribute("aria-label", "Region");
    expect(wrapper.className).toContain("extra");
    expect(wrapper.className).toContain("max-w-page");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <PageContainer>
        <h1>Title</h1>
        <p>Body</p>
      </PageContainer>,
    );
    await expectNoA11yViolations(container);
  });
});
