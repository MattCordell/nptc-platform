import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { RadioGroup } from "./radio-group.tsx";

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "review", label: "In review" },
  { value: "retired", label: "Retired", disabled: true },
  { value: "published", label: "Published" },
];

function ControlledRadioGroup({ initial }: { initial?: string }) {
  const [value, setValue] = useState<string | undefined>(initial);
  return (
    <RadioGroup
      legend="Status"
      options={STATUS_OPTIONS}
      value={value}
      onChange={setValue}
    />
  );
}

describe("RadioGroup", () => {
  it("labels the group with its legend, not with adjacent text", () => {
    render(<ControlledRadioGroup />);

    expect(screen.getByRole("group", { name: "Status" })).toBeInTheDocument();
  });

  it("is a single tab stop, not one stop per option", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ControlledRadioGroup />
        <button type="button">After the group</button>
      </>,
    );

    // Nothing selected yet: the first enabled option carries the tab stop,
    // so the group is reachable at all.
    await user.tab();
    expect(screen.getByLabelText("Draft")).toHaveFocus();

    // One more Tab leaves the group entirely rather than walking its options.
    await user.tab();
    expect(screen.getByRole("button", { name: "After the group" })).toHaveFocus();
  });

  it("moves the single tab stop to the selected option once there is one", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup initial="published" />);

    await user.tab();

    expect(screen.getByLabelText("Published")).toHaveFocus();
    expect(screen.getByLabelText("Draft")).toHaveAttribute("tabindex", "-1");
  });

  it("traverses with the arrow keys, moving selection and focus together", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup initial="draft" />);

    await user.tab();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByLabelText("In review")).toHaveFocus();
    expect(screen.getByLabelText("In review")).toBeChecked();

    await user.keyboard("{ArrowUp}");
    expect(screen.getByLabelText("Draft")).toHaveFocus();
    expect(screen.getByLabelText("Draft")).toBeChecked();

    // Right/Left are equivalent to Down/Up, as they are for native radios.
    await user.keyboard("{ArrowRight}");
    expect(screen.getByLabelText("In review")).toBeChecked();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByLabelText("Draft")).toBeChecked();
  });

  it("skips a disabled option when traversing", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup initial="review" />);

    await user.tab();
    await user.keyboard("{ArrowDown}");

    // "Retired" sits between "In review" and "Published" and is disabled.
    expect(screen.getByLabelText("Retired")).not.toBeChecked();
    expect(screen.getByLabelText("Published")).toHaveFocus();
    expect(screen.getByLabelText("Published")).toBeChecked();
  });

  it("wraps at both ends", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup initial="published" />);

    await user.tab();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByLabelText("Draft")).toBeChecked();

    await user.keyboard("{ArrowUp}");
    expect(screen.getByLabelText("Published")).toBeChecked();
  });

  it("jumps to the first and last enabled option with Home and End", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup initial="review" />);

    await user.tab();
    await user.keyboard("{End}");
    expect(screen.getByLabelText("Published")).toBeChecked();
    expect(screen.getByLabelText("Published")).toHaveFocus();

    await user.keyboard("{Home}");
    expect(screen.getByLabelText("Draft")).toBeChecked();
    expect(screen.getByLabelText("Draft")).toHaveFocus();
  });

  it("selects on click as well as by keyboard", async () => {
    const user = userEvent.setup();
    render(<ControlledRadioGroup />);

    await user.click(screen.getByLabelText("In review"));

    expect(screen.getByLabelText("In review")).toBeChecked();
  });

  it("puts a caller-supplied id on the first option's input, so a summary link lands on a real control", () => {
    render(
      <RadioGroup
        id="status"
        legend="Status"
        options={STATUS_OPTIONS}
        value={undefined}
        onChange={vi.fn()}
        error="Choose a status"
      />,
    );

    expect(screen.getByLabelText("Draft")).toHaveAttribute("id", "status");
    expect(screen.getByLabelText("In review")).toHaveAttribute("id", "status-1");
    expect(screen.getByText("Choose a status")).toHaveAttribute("id", "status-error");
  });

  it("describes the group by its hint and error, and marks it invalid", () => {
    render(
      <RadioGroup
        legend="Status"
        options={STATUS_OPTIONS}
        value={undefined}
        onChange={vi.fn()}
        hint="Only published entries are exported"
        error="Choose a status"
      />,
    );

    const group = screen.getByRole("group", { name: "Status" });
    expect(group).toHaveAttribute("aria-invalid", "true");
    expect(group.getAttribute("aria-describedby")).toBe(
      `${screen.getByText("Only published entries are exported").id} ${screen.getByText("Choose a status").id}`,
    );
  });

  it("gives two groups on one screen distinct names, so they do not behave as one group", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ControlledRadioGroup />
        <ControlledRadioGroup />
      </>,
    );

    const [firstDraft, secondDraft] = screen.getAllByLabelText("Draft");
    expect(firstDraft.getAttribute("name")).not.toBe(secondDraft.getAttribute("name"));

    await user.click(firstDraft);
    await user.click(secondDraft);
    expect(firstDraft).toBeChecked();
    expect(secondDraft).toBeChecked();
  });

  it("is not a tab stop at all when every option is disabled", async () => {
    const user = userEvent.setup();
    render(
      <>
        <RadioGroup
          legend="Status"
          options={[
            { value: "draft", label: "Draft", disabled: true },
            { value: "published", label: "Published", disabled: true },
          ]}
          value={undefined}
          onChange={vi.fn()}
        />
        <button type="button">After the group</button>
      </>,
    );

    await user.tab();

    // A group with nothing selectable in it is a dead stop, not a stop that
    // looks reachable and then refuses every key.
    expect(screen.getByRole("button", { name: "After the group" })).toHaveFocus();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <RadioGroup
        legend="Status"
        options={STATUS_OPTIONS}
        value="draft"
        onChange={vi.fn()}
        hint="Only published entries are exported"
        error="Choose a status"
      />,
    );

    await expectNoA11yViolations(container);
  });
});
