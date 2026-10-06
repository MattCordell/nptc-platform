import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { Pagination } from "./pagination.tsx";

const previous = () => screen.getByRole("button", { name: "Previous page" });
const next = () => screen.getByRole("button", { name: "Next page" });

describe("Pagination", () => {
  it("renders a navigation landmark with Previous and Next, and no page numbers", () => {
    render(<Pagination hasNext onNext={() => undefined} onPrevious={() => undefined} />);

    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(nav).toContainElement(previous());
    expect(nav).toContainElement(next());
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(nav).not.toHaveTextContent(/\d/);
  });

  it("takes a caller-supplied landmark name", () => {
    render(<Pagination label="Audit events" hasNext onNext={() => undefined} />);

    expect(screen.getByRole("navigation", { name: "Audit events" })).toBeInTheDocument();
  });

  it("calls onNext and onPrevious when each is available", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const onPrevious = vi.fn();
    render(<Pagination hasNext onNext={onNext} onPrevious={onPrevious} />);

    await user.click(next());
    await user.click(previous());

    expect(onNext).toHaveBeenCalledOnce();
    expect(onPrevious).toHaveBeenCalledOnce();
  });

  it("says in text that there are no more results when hasNext is false", () => {
    render(<Pagination hasNext={false} onNext={() => undefined} />);

    const message = screen.getByText("No more results");
    expect(message).toBeVisible();
    expect(next()).toHaveAttribute("aria-disabled", "true");
    expect(next()).toHaveAttribute("aria-describedby", message.id);
  });

  it("shows no end message while a next page exists", () => {
    render(<Pagination hasNext onNext={() => undefined} />);

    expect(screen.queryByText("No more results")).not.toBeInTheDocument();
    expect(next()).toHaveAttribute("aria-disabled", "false");
    expect(next()).not.toHaveAttribute("aria-describedby");
  });

  it("does not call onNext when there is no next page", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(<Pagination hasNext={false} onNext={onNext} />);

    await user.click(next());
    next().focus();
    await user.keyboard("{Enter}");

    expect(onNext).not.toHaveBeenCalled();
  });

  it("marks Previous unavailable and inert when onPrevious is absent", async () => {
    const user = userEvent.setup();
    render(<Pagination hasNext onNext={() => undefined} />);

    expect(previous()).toHaveAttribute("aria-disabled", "true");
    await user.click(previous());
    expect(previous()).toBeInTheDocument();
  });

  it("keeps an unavailable button focusable, so focus is not stranded", async () => {
    const user = userEvent.setup();
    render(<Pagination hasNext={false} onNext={() => undefined} />);

    await user.tab();
    expect(previous()).toHaveFocus();
    await user.tab();
    expect(next()).toHaveFocus();
  });

  it("is operable by keyboard alone: Tab to a button, Enter or Space to press it", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const onPrevious = vi.fn();
    render(<Pagination hasNext onNext={onNext} onPrevious={onPrevious} />);

    await user.tab();
    expect(previous()).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onPrevious).toHaveBeenCalledOnce();

    await user.tab();
    expect(next()).toHaveFocus();
    await user.keyboard("{ }");
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("passes native props and className through to the nav", () => {
    render(
      <Pagination
        hasNext
        onNext={() => undefined}
        className="extra-class"
        data-testid="pager"
      />,
    );

    const nav = screen.getByTestId("pager");
    expect(nav.tagName).toBe("NAV");
    expect(nav.className).toContain("extra-class");
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(<Pagination hasNext={false} onNext={() => undefined} />);

    for (const element of container.querySelectorAll("*")) {
      expectTokenClassesOnly(element.getAttribute("class") ?? "");
    }
  });

  it("keeps both buttons at least 40px tall, above the 24px minimum target", () => {
    render(<Pagination hasNext onNext={() => undefined} />);

    expect(previous().className).toContain("min-h-10");
    expect(next().className).toContain("min-h-10");
  });

  it.each([
    ["a middle page", true, () => undefined],
    ["the last page", false, () => undefined],
    ["the first page", true, undefined],
    ["a single page", false, undefined],
  ])("has no automated accessibility violations on %s", async (_name, hasNext, back) => {
    const { container } = render(
      <Pagination hasNext={hasNext} onNext={() => undefined} onPrevious={back} />,
    );

    await expectNoA11yViolations(container);
  });
});
