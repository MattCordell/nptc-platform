import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { PageHeader } from "./page-header.tsx";

describe("PageHeader", () => {
  it("renders the title as the page's single h1", () => {
    render(<PageHeader title="Catalogue" />);

    const headings = screen.getAllByRole("heading");
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveProperty("tagName", "H1");
    expect(headings[0]).toHaveTextContent("Catalogue");
  });

  it("puts the id on the h1 so aria-labelledby can reference it", () => {
    render(
      <section aria-labelledby="page-title">
        <PageHeader id="page-title" title="Catalogue" />
      </section>,
    );

    expect(screen.getByRole("region", { name: "Catalogue" })).toBeInTheDocument();
  });

  it("sets no id on the h1 when none is given", () => {
    render(<PageHeader title="Catalogue" />);
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveAttribute("id");
  });

  it("renders the meta line and the actions when given", () => {
    render(
      <PageHeader
        title="Catalogue"
        meta="12 terms"
        actions={<button type="button">Add</button>}
      />,
    );

    expect(screen.getByText("12 terms")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("renders no empty meta or actions elements when they are absent", () => {
    const { container } = render(<PageHeader title="Catalogue" />);

    const row = container.firstElementChild as HTMLElement;
    expect(row.children).toHaveLength(1);
    expect(row.firstElementChild?.children).toHaveLength(1);
  });

  it("renders a meta of zero, since a count of none is still a count", () => {
    render(<PageHeader title="Catalogue" meta={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it.each([
    ["null", null],
    ["false", false],
    ["an empty string", ""],
    ["an empty array", []],
    ["an array of nothing", [null, false]],
  ])("renders no meta or actions element for %s", (_label, empty) => {
    const { container } = render(
      <PageHeader title="Catalogue" meta={empty} actions={empty} />,
    );

    const row = container.firstElementChild as HTMLElement;
    expect(row.children).toHaveLength(1);
    expect(row.firstElementChild?.children).toHaveLength(1);
  });

  it("places the actions after the title in DOM order", () => {
    render(<PageHeader title="Catalogue" actions={<button type="button">Add</button>} />);

    const heading = screen.getByRole("heading", { level: 1 });
    const button = screen.getByRole("button", { name: "Add" });
    expect(
      heading.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("does not render a header element, which app.css pads globally", () => {
    const { container } = render(<PageHeader title="Catalogue" />);
    expect(container.querySelector("header")).toBeNull();
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(
      <PageHeader title="Catalogue" meta="12 terms" actions={<span>Action</span>} />,
    );

    for (const element of container.querySelectorAll("*")) {
      expectTokenClassesOnly(element.getAttribute("class") ?? "");
    }
  });

  it("takes its text colours from tokens", () => {
    render(<PageHeader title="Catalogue" meta="12 terms" />);

    expect(screen.getByRole("heading", { level: 1 }).className).toContain(
      "text-[var(--color-text)]",
    );
    expect(screen.getByText("12 terms").className).toContain(
      "text-[var(--color-text-muted)]",
    );
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <PageHeader
        id="page-title"
        title="Catalogue"
        meta="12 terms"
        actions={<button type="button">Add</button>}
      />,
    );
    await expectNoA11yViolations(container);
  });
});
