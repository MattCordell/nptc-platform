import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { Breadcrumb } from "./breadcrumb.tsx";

function renderTrail() {
  return render(
    <Breadcrumb
      ancestors={[<a href="/">Home</a>, <a href="/catalogue">Catalogue</a>]}
      current="NPTC-000247"
    />,
  );
}

describe("Breadcrumb", () => {
  it("is a labelled navigation landmark holding an ordered list", () => {
    renderTrail();

    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const items = within(within(nav).getByRole("list")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Home/",
      "Catalogue/",
      "NPTC-000247",
    ]);
  });

  it("marks the current page and leaves it as plain text", () => {
    renderTrail();

    const current = screen.getByText("NPTC-000247");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(within(current).queryByRole("link")).toBeNull();
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Home",
      "Catalogue",
    ]);
  });

  it("hides the separators from assistive technology", () => {
    const { container } = renderTrail();

    const separators = container.querySelectorAll("li > span[aria-hidden='true']");
    expect(separators).toHaveLength(2);
  });

  it("gives each link a target of at least 24px tall", () => {
    const { container } = renderTrail();

    for (const wrapper of container.querySelectorAll("li > span:not([aria-hidden])")) {
      expect(wrapper.className).toContain("[&_a]:min-h-6");
    }
    expect(screen.getByText("NPTC-000247").className).toContain("min-h-6");
  });

  it("uses design tokens only", () => {
    const { container } = renderTrail();

    expectTokenClassesOnly(container.innerHTML.match(/class="[^"]*"/g)?.join(" ") ?? "");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = renderTrail();
    await expectNoA11yViolations(container);
  });
});
