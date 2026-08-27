import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { CheckboxGroup } from "./checkbox-group.tsx";

const SPECIMEN_OPTIONS = [
  { value: "serum", label: "Serum" },
  { value: "plasma", label: "Plasma" },
  { value: "urine", label: "Urine" },
  { value: "csf", label: "CSF", disabled: true },
];

function ControlledCheckboxGroup({ initial = [] as string[] }) {
  const [value, setValue] = useState(initial);
  return (
    <CheckboxGroup
      legend="Specimen types"
      options={SPECIMEN_OPTIONS}
      value={value}
      onChange={setValue}
    />
  );
}

describe("CheckboxGroup", () => {
  it("labels the group with its legend, not with adjacent text", () => {
    render(<ControlledCheckboxGroup />);

    expect(screen.getByRole("group", { name: "Specimen types" })).toBeInTheDocument();
  });

  it("ticks and unticks an option without disturbing the others", async () => {
    const user = userEvent.setup();
    render(<ControlledCheckboxGroup initial={["serum"]} />);

    await user.click(screen.getByLabelText("Urine"));
    expect(screen.getByLabelText("Serum")).toBeChecked();
    expect(screen.getByLabelText("Urine")).toBeChecked();

    await user.click(screen.getByLabelText("Serum"));
    expect(screen.getByLabelText("Serum")).not.toBeChecked();
    expect(screen.getByLabelText("Urine")).toBeChecked();
  });

  it("reports the value in option order, not in the order boxes were ticked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CheckboxGroup
        legend="Specimen types"
        options={SPECIMEN_OPTIONS}
        value={["urine"]}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByLabelText("Serum"));

    expect(onChange).toHaveBeenCalledWith(["serum", "urine"]);
  });

  it("keeps a tab stop per box, because each is an independent answer", async () => {
    const user = userEvent.setup();
    render(<ControlledCheckboxGroup />);

    await user.tab();
    expect(screen.getByLabelText("Serum")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("Plasma")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("Urine")).toHaveFocus();
    // The disabled option is skipped rather than being a dead stop.
    await user.tab();
    expect(screen.getByLabelText("CSF")).not.toHaveFocus();
  });

  it("puts a caller-supplied id on the first option's input, so a summary link lands on a real control", () => {
    render(
      <CheckboxGroup
        id="specimen-types"
        legend="Specimen types"
        options={SPECIMEN_OPTIONS}
        value={[]}
        onChange={vi.fn()}
        error="Choose at least one"
      />,
    );

    expect(screen.getByLabelText("Serum")).toHaveAttribute("id", "specimen-types");
    expect(screen.getByLabelText("Plasma")).toHaveAttribute("id", "specimen-types-1");
    expect(screen.getByText("Choose at least one")).toHaveAttribute(
      "id",
      "specimen-types-error",
    );
  });

  it("describes each box by the group's hint and error, so the text is announced at all", () => {
    render(
      <CheckboxGroup
        legend="Specimen types"
        options={SPECIMEN_OPTIONS}
        value={[]}
        onChange={vi.fn()}
        hint="Choose every type the test accepts"
        error="Choose at least one"
      />,
    );

    // Asserted as an accessible description on the control, not as an
    // attribute on the fieldset: a group role's description is
    // inconsistently announced, so an attribute there can pass a test while
    // the user hears nothing.
    for (const label of ["Serum", "Plasma", "Urine"]) {
      expect(screen.getByLabelText(label)).toHaveAccessibleDescription(
        "Choose every type the test accepts Choose at least one",
      );
      expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "true");
    }
    // And not duplicated onto the group, which would announce it twice on
    // the first option.
    const group = screen.getByRole("group", { name: "Specimen types" });
    expect(group).not.toHaveAttribute("aria-describedby");
    expect(group).not.toHaveAttribute("aria-invalid");
  });

  it("carries through a held value that is no longer an offered option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CheckboxGroup
        legend="Specimen types"
        options={SPECIMEN_OPTIONS}
        value={["retired-specimen-code", "urine"]}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByLabelText("Serum"));

    // A stored entry can hold a code the list no longer offers. Ticking an
    // unrelated box must not delete it.
    expect(onChange).toHaveBeenCalledWith(["serum", "urine", "retired-specimen-code"]);
  });

  it("gives two groups on one screen distinct names, so they do not behave as one", () => {
    render(
      <>
        <ControlledCheckboxGroup />
        <ControlledCheckboxGroup />
      </>,
    );

    const [first, second] = screen.getAllByLabelText("Serum");
    expect(first).toHaveAttribute("name");
    expect(first.getAttribute("name")).not.toBe(second.getAttribute("name"));
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <CheckboxGroup
        legend="Specimen types"
        options={SPECIMEN_OPTIONS}
        value={["serum"]}
        onChange={vi.fn()}
        hint="Choose every type the test accepts"
        error="Choose at least one"
      />,
    );

    await expectNoA11yViolations(container);
  });
});
