import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Checkbox } from "./checkbox.tsx";

describe("Checkbox", () => {
  it("associates the label with the box via a generated id", () => {
    render(<Checkbox label="Published" />);

    const checkbox = screen.getByLabelText("Published");
    expect(checkbox).toHaveAttribute("type", "checkbox");
  });

  it("links the hint via aria-describedby, with no aria-invalid when there is no error", () => {
    render(<Checkbox label="Published" hint="Visible in the next release" />);

    const checkbox = screen.getByLabelText("Published");
    expect(checkbox).toHaveAttribute(
      "aria-describedby",
      screen.getByText("Visible in the next release").id,
    );
    expect(checkbox).not.toHaveAttribute("aria-invalid");
  });

  it("marks the box invalid and describes it by both hint and error", () => {
    render(<Checkbox label="Published" hint="A hint" error="You must confirm this" />);

    const checkbox = screen.getByLabelText("Published");
    expect(checkbox).toHaveAttribute("aria-invalid", "true");
    expect(checkbox.getAttribute("aria-describedby")).toBe(
      `${screen.getByText("A hint").id} ${screen.getByText("You must confirm this").id}`,
    );
    // Same reasoning as Field's error text: describedby already announces it.
    expect(screen.getByText("You must confirm this")).not.toHaveAttribute(
      "role",
      "alert",
    );
  });

  it("puts a caller-supplied id on the input, for an error summary to link to", () => {
    render(<Checkbox id="published" label="Published" error="Required" />);

    expect(screen.getByLabelText("Published")).toHaveAttribute("id", "published");
    expect(screen.getByText("Required")).toHaveAttribute("id", "published-error");
  });

  it("is reachable by Tab and togglable with the space bar", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Checkbox label="Published" onChange={onChange} />);

    await user.tab();
    expect(screen.getByLabelText("Published")).toHaveFocus();

    await user.keyboard("{ }");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Published")).toBeChecked();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <Checkbox label="Published" hint="A hint" error="You must confirm this" />,
    );

    await expectNoA11yViolations(container);
  });
});
