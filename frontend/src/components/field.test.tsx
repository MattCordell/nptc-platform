import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Field } from "./field.tsx";

describe("Field", () => {
  it("associates the label with the control via a generated id", () => {
    render(
      <Field label="Requesting term">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>,
    );

    const input = screen.getByLabelText("Requesting term");
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe("INPUT");
  });

  it("links the hint via aria-describedby, with no aria-invalid when there is no error", () => {
    render(
      <Field label="Requesting term" hint="As it appears on the request form">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>,
    );

    const input = screen.getByLabelText("Requesting term");
    const hint = screen.getByText("As it appears on the request form");
    expect(input).toHaveAttribute("aria-describedby", hint.id);
    expect(input).not.toHaveAttribute("aria-invalid");
  });

  it("marks the control invalid and describes it by the error when in an error state", () => {
    render(
      <Field label="Requesting term" error="This field is required">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>,
    );

    const input = screen.getByLabelText("Requesting term");
    const error = screen.getByText("This field is required");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", error.id);
    // Not role="alert" - the description is already reachable via
    // aria-describedby, and an alert role on top of that double-announces
    // it. A submit-time validation summary announces via LiveRegion instead.
    expect(error).not.toHaveAttribute("role", "alert");
  });

  it("describes the control by both hint and error when both are present", () => {
    render(
      <Field
        label="Requesting term"
        hint="As it appears on the request form"
        error="Required"
      >
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>,
    );

    const input = screen.getByLabelText("Requesting term");
    const hint = screen.getByText("As it appears on the request form");
    const error = screen.getByText("Required");
    expect(input.getAttribute("aria-describedby")).toBe(`${hint.id} ${error.id}`);
  });

  it("composes with a select control, not just input", () => {
    render(
      <Field label="Status">
        {(controlProps) => (
          <select {...controlProps}>
            <option value="active">Active</option>
            <option value="retired">Retired</option>
          </select>
        )}
      </Field>,
    );

    expect(screen.getByLabelText("Status").tagName).toBe("SELECT");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <Field label="Requesting term" hint="A hint" error="An error">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>,
    );

    await expectNoA11yViolations(container);
  });
});
