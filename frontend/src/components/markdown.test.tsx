import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { Markdown } from "./markdown.tsx";

/**
 * NFR-47: the terms text is Markdown served by the API. The renderer builds
 * elements only, so the principal failure mode is text that would run or
 * link somewhere it should not.
 */

describe("Markdown", () => {
  it("renders headings one level below the page's h1", () => {
    render(<Markdown text={"# Terms\n\n## 1. Using the platform\n\n### Detail"} />);

    expect(screen.getByRole("heading", { level: 2, name: "Terms" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "1. Using the platform" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 4, name: "Detail" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("caps heading depth at h6 and honours a different offset", () => {
    render(<Markdown text={"###### Deep"} headingOffset={3} />);

    expect(screen.getByRole("heading", { level: 6, name: "Deep" })).toBeInTheDocument();
  });

  it("joins wrapped lines into one paragraph and splits on a blank line", () => {
    const { container } = render(
      <Markdown text={"You may read the\ncatalogue.\n\nSecond paragraph."} />,
    );

    const paragraphs = container.querySelectorAll("p");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("You may read the catalogue.");
  });

  it("renders bulleted and numbered lists, with a wrapped line staying in its item", () => {
    render(<Markdown text={"- first\n- second\n  continued\n\n1. one\n2. two"} />);

    const lists = screen.getAllByRole("list");
    expect(lists).toHaveLength(2);
    expect(lists[0]!.tagName).toBe("UL");
    expect(lists[1]!.tagName).toBe("OL");
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["first", "second continued", "one", "two"]);
  });

  it("renders bold, italic and code inline", () => {
    const { container } = render(
      <Markdown text={"**Temporary text.** Some *emphasis* and `code`."} />,
    );

    expect(container.querySelector("strong")).toHaveTextContent("Temporary text.");
    expect(container.querySelector("em")).toHaveTextContent("emphasis");
    expect(container.querySelector("code")).toHaveTextContent("code");
  });

  it("links http, https and mailto targets", () => {
    render(
      <Markdown
        text={
          "[site](https://example.org/a) [mail](mailto:help@example.org) [plain](http://example.org)"
        }
      />,
    );

    expect(screen.getByRole("link", { name: "site" })).toHaveAttribute(
      "href",
      "https://example.org/a",
    );
    expect(screen.getByRole("link", { name: "site" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
    expect(screen.getByRole("link", { name: "mail" })).toHaveAttribute(
      "href",
      "mailto:help@example.org",
    );
    expect(screen.getByRole("link", { name: "plain" })).toBeInTheDocument();
  });

  // Principal failure mode: a link target that would run script, or a relative
  // path that would reload the SPA, shows as text and is not clickable.
  it("leaves a javascript: or relative link target as plain text", () => {
    render(<Markdown text={"[click](javascript:alert(1)) and [policy](/privacy)"} />);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText(/\[click\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[policy\]\(\/privacy\)/)).toBeInTheDocument();
  });

  it("shows raw HTML as text and creates no element from it", () => {
    const { container } = render(
      <Markdown text={'<script>alert(1)</script> <img src=x onerror="alert(1)">'} />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(1)</script>");
  });

  it("renders nothing for empty text", () => {
    const { container } = render(<Markdown text={""} />);

    expect(container.firstElementChild?.children).toHaveLength(0);
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(
      <Markdown text={"# H\n\n- a\n\n[x](https://example.org) `c`"} />,
    );

    for (const element of container.querySelectorAll("[class]")) {
      expectTokenClassesOnly(element.className);
    }
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <Markdown
        text={"# Terms\n\n## One\n\nText with [a link](https://example.org).\n\n- item"}
      />,
    );

    await expectNoA11yViolations(container);
  });
});
