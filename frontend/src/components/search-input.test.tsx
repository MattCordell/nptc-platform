import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { SearchInput } from "./search-input.tsx";

function Harness({
  initial = "",
  onSubmit = () => undefined,
  submitLabel,
}: {
  initial?: string;
  onSubmit?: (value: string) => void;
  submitLabel?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    <SearchInput
      label="Search term or code"
      hint="A term, synonym or SNOMED CT code"
      value={value}
      onValueChange={setValue}
      onSubmit={onSubmit}
      submitLabel={submitLabel}
    />
  );
}

describe("SearchInput", () => {
  it("renders a search landmark holding a labelled input", () => {
    render(<Harness />);

    const form = screen.getByRole("search");
    expect(form).toContainElement(screen.getByLabelText("Search term or code"));
    expect(screen.getByRole("searchbox", { name: "Search term or code" })).toBeVisible();
  });

  it("describes the input by its hint", () => {
    render(<Harness />);

    const input = screen.getByRole("searchbox");
    const hint = screen.getByText("A term, synonym or SNOMED CT code");
    expect(input).toHaveAttribute("aria-describedby", hint.id);
  });

  it("shows the value it is given and reports each edit", async () => {
    const user = userEvent.setup();
    render(<Harness initial="fer" />);

    const input = screen.getByRole("searchbox");
    expect(input).toHaveValue("fer");

    await user.type(input, "ritin");
    expect(input).toHaveValue("ferritin");
  });

  it("submits the trimmed value from the button", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.type(screen.getByRole("searchbox"), "  ferritin  ");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("ferritin");
  });

  it("submits on Enter from the input", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.type(screen.getByRole("searchbox"), "49466006{Enter}");

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("49466006");
  });

  it("submits an empty string when the box is blank, so a caller can clear its query", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness initial="   " onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("");
  });

  it("uses a caller-supplied submit label", () => {
    render(<Harness submitLabel="Find" />);

    expect(screen.getByRole("button", { name: "Find" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Search" })).not.toBeInTheDocument();
  });

  it("puts a caller-supplied id on the input and merges className onto the form", () => {
    render(
      <SearchInput
        id="catalogue-query"
        className="extra-class"
        label="Search"
        value=""
        onValueChange={() => undefined}
        onSubmit={() => undefined}
      />,
    );

    expect(screen.getByRole("searchbox")).toHaveAttribute("id", "catalogue-query");
    expect(screen.getByRole("search").className).toContain("extra-class");
  });

  it("is reachable and operable by keyboard alone: input, then button", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.tab();
    expect(screen.getByRole("searchbox")).toHaveFocus();
    await user.keyboard("ferritin");

    await user.tab();
    const button = screen.getByRole("button", { name: "Search" });
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("ferritin");
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(<Harness />);

    for (const element of container.querySelectorAll("*")) {
      expectTokenClassesOnly(element.getAttribute("class") ?? "");
    }
  });

  it("keeps the input and button at least 40px tall, above the 24px minimum target", () => {
    render(<Harness />);

    expect(screen.getByRole("searchbox").className).toContain("min-h-10");
    expect(screen.getByRole("button", { name: "Search" }).className).toContain(
      "min-h-10",
    );
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<Harness initial="ferritin" />);

    await expectNoA11yViolations(container);
  });
});
