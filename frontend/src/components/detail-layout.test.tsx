import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { DetailLayout } from "./detail-layout.tsx";

function renderLayout() {
  return render(
    <main>
      <DetailLayout sidebar={<p>Side content</p>} sidebarLabel="Entry details">
        <p>Main content</p>
      </DetailLayout>
    </main>,
  );
}

describe("DetailLayout", () => {
  it("renders the main column and a named sidebar landmark", () => {
    renderLayout();

    expect(screen.getByText("Main content")).toBeInTheDocument();
    const sidebar = screen.getByRole("complementary", { name: "Entry details" });
    expect(within(sidebar).getByText("Side content")).toBeInTheDocument();
  });

  it("reads the main column before the sidebar", () => {
    renderLayout();

    const main = screen.getByText("Main content");
    const side = screen.getByText("Side content");
    expect(main.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  // Class-contract assertion, not rendered layout: jsdom does not resolve
  // Tailwind utilities.
  it("is one column on a narrow screen and two from the large breakpoint", () => {
    const { container } = renderLayout();

    const grid = container.querySelector("main > div") as HTMLElement;
    expect(grid.className).toContain("grid");
    expect(grid.className).toContain("lg:grid-cols-[minmax(0,1fr)_20rem]");
  });

  it("uses design tokens only", () => {
    const { container } = renderLayout();

    expectTokenClassesOnly(container.innerHTML.match(/class="[^"]*"/g)?.join(" ") ?? "");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = renderLayout();
    await expectNoA11yViolations(container);
  });
});
