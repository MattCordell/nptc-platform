import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { NoticePage } from "./notice-page.tsx";

describe("NoticePage", () => {
  it("renders exactly one h1 with the title", () => {
    render(
      <NoticePage title="Page title">
        <p>Body</p>
      </NoticePage>,
    );

    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Page title");
  });

  it("puts a caller's id on the h1", () => {
    render(
      <NoticePage title="Page title" id="my-heading">
        <p>Body</p>
      </NoticePage>,
    );

    expect(screen.getByRole("heading", { level: 1 })).toHaveAttribute("id", "my-heading");
  });

  it("renders actions after the body in reading order", () => {
    render(
      <NoticePage title="Page title" actions={<a href="/somewhere">Go somewhere</a>}>
        <p>Body</p>
      </NoticePage>,
    );

    const body = screen.getByText("Body");
    const action = screen.getByRole("link", { name: "Go somewhere" });
    expect(body.compareDocumentPosition(action)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it.each([undefined, null, false])(
    "renders no actions wrapper when actions is %s",
    (actions) => {
      const { container } = render(
        <NoticePage title="Page title" actions={actions}>
          <p>Body</p>
        </NoticePage>,
      );

      // The card is the last block; with no actions it holds only the body.
      const card = container.firstElementChild?.lastElementChild;
      expect(card?.children).toHaveLength(1);
    },
  );

  it("makes the h1 focusable only when asked to move focus to it", () => {
    const { rerender } = render(
      <NoticePage title="Page title">
        <p>Body</p>
      </NoticePage>,
    );
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveAttribute("tabindex");

    rerender(
      <NoticePage title="Page title" focusHeading>
        <p>Body</p>
      </NoticePage>,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveAttribute("tabindex", "-1");
  });

  it("leaves focus alone unless asked to move it", () => {
    render(
      <NoticePage title="Page title">
        <p>Body</p>
      </NoticePage>,
    );

    expect(document.activeElement).toBe(document.body);
  });

  it("moves focus to the h1 on mount when asked", () => {
    render(
      <NoticePage title="Page title" focusHeading>
        <p>Body</p>
      </NoticePage>,
    );

    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(
      <NoticePage title="Page title" actions={<a href="/somewhere">Go somewhere</a>}>
        <p>Body</p>
      </NoticePage>,
    );

    for (const element of container.querySelectorAll("[class]")) {
      expectTokenClassesOnly(element.className);
    }
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <NoticePage title="Page title" actions={<a href="/somewhere">Go somewhere</a>}>
        <p>Body</p>
      </NoticePage>,
    );

    await expectNoA11yViolations(container);
  });
});
