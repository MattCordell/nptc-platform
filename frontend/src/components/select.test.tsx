import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Select } from "./select.tsx";

const STATUS_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "retired", label: "Retired" },
  { value: "draft", label: "Draft", disabled: true },
];

describe("Select", () => {
  it("associates the label with the select", () => {
    render(<Select label="Status" options={STATUS_OPTIONS} />);

    const select = screen.getByLabelText("Status");
    expect(select.tagName).toBe("SELECT");
  });

  it("renders the options in the order given, carrying each option's disabled state", () => {
    render(<Select label="Status" options={STATUS_OPTIONS} />);

    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Active",
      "Retired",
      "Draft",
    ]);
    expect(options[2]).toBeDisabled();
  });

  it("reports the chosen value through onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Select label="Status" options={STATUS_OPTIONS} onChange={onChange} />);

    await user.selectOptions(screen.getByLabelText("Status"), "retired");

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Status")).toHaveValue("retired");
  });

  it("renders a placeholder as an empty first option, so an untouched select is not silently answered", () => {
    render(<Select label="Status" options={STATUS_OPTIONS} placeholder="Choose a status" />);

    const placeholder = screen.getByRole("option", { name: "Choose a status" });
    expect(placeholder).toHaveValue("");
    // The placeholder must NOT be disabled: the selectedness algorithm skips
    // disabled options, so a disabled placeholder would be passed over and
    // "Active" selected - the exact defect the placeholder prevents.
    expect(placeholder).not.toBeDisabled();
    expect(screen.getByLabelText("Status")).toHaveValue("");
  });

  it("marks the select invalid and describes it by the error", () => {
    render(<Select label="Status" error="Choose a status" options={STATUS_OPTIONS} />);

    const select = screen.getByLabelText("Status");
    const error = screen.getByText("Choose a status");
    expect(select).toHaveAttribute("aria-invalid", "true");
    expect(select).toHaveAttribute("aria-describedby", error.id);
  });

  it("puts a caller-supplied id on the select itself", () => {
    render(<Select id="status" label="Status" options={STATUS_OPTIONS} />);

    expect(screen.getByLabelText("Status")).toHaveAttribute("id", "status");
  });

  it("is reachable by keyboard alone", async () => {
    const user = userEvent.setup();
    render(<Select label="Status" options={STATUS_OPTIONS} />);

    await user.tab();

    expect(screen.getByLabelText("Status")).toHaveFocus();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <Select
        label="Status"
        hint="Only active entries are published"
        error="Choose a status"
        placeholder="Choose a status"
        options={STATUS_OPTIONS}
      />,
    );

    await expectNoA11yViolations(container);
  });
});
