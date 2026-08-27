import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useId } from "react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { ErrorSummary } from "./error-summary.tsx";
import { Field } from "./field.tsx";

const ERRORS = [
  { fieldId: "requesting-term", message: "Enter a requesting term" },
  { fieldId: "status", message: "Choose a status" },
];

describe("ErrorSummary", () => {
  it("renders nothing when there is neither a field error nor a form error", () => {
    const { container } = render(<ErrorSummary errors={[]} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("lists one link per error, named by its message", () => {
    render(<ErrorSummary errors={ERRORS} />);

    expect(
      screen.getByRole("heading", { name: "There is a problem" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "#requesting-term",
      "#status",
    ]);
    expect(screen.getByRole("link", { name: "Choose a status" })).toBeInTheDocument();
  });

  it("renders a form-level error without a link, since there is nowhere to send focus", () => {
    render(<ErrorSummary errors={[]} formError="The catalogue rejected this entry." />);

    expect(screen.getByText("The catalogue rejected this entry.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("moves focus to the named field when an item is activated, without navigating", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ErrorSummary errors={ERRORS} />
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </>,
    );

    await user.click(screen.getByRole("link", { name: "Enter a requesting term" }));

    expect(screen.getByLabelText("Requesting term")).toHaveFocus();
    // The default navigation is suppressed: a bare hash is a route change
    // for this app's router, and the focus above is what the link is for.
    expect(window.location.hash).toBe("");
  });

  it("is focusable programmatically but is not an alert, so it announces once not twice", () => {
    render(<ErrorSummary errors={ERRORS} />);

    const summary = screen
      .getByRole("heading", { name: "There is a problem" })
      .closest("div");
    expect(summary).toHaveAttribute("tabindex", "-1");
    expect(summary).not.toHaveAttribute("role", "alert");

    summary?.focus();
    expect(summary).toHaveFocus();
  });

  it("takes a caller-supplied title", () => {
    render(<ErrorSummary errors={ERRORS} title="This entry could not be saved" />);

    expect(
      screen.getByRole("heading", { name: "This entry could not be saved" }),
    ).toBeInTheDocument();
  });

  it("lists a control that failed two ways as two items", () => {
    render(
      <ErrorSummary
        errors={[
          { fieldId: "requesting-term", message: "Enter a requesting term" },
          { fieldId: "requesting-term", message: "Use fewer than 255 characters" },
        ]}
      />,
    );

    expect(screen.getAllByRole("link")).toHaveLength(2);
  });

  it("takes a heading level, for a form that is not the whole page", () => {
    render(<ErrorSummary errors={ERRORS} headingLevel={3} />);

    expect(
      screen.getByRole("heading", { level: 3, name: "There is a problem" }),
    ).toBeInTheDocument();
  });

  it("focuses a field whose id was generated rather than supplied", async () => {
    const user = userEvent.setup();

    function GeneratedIdDemo() {
      const generated = useId();
      return (
        <>
          <ErrorSummary
            errors={[{ fieldId: generated, message: "Enter a requesting term" }]}
          />
          <label htmlFor={generated}>Requesting term</label>
          <input id={generated} type="text" />
        </>
      );
    }

    render(<GeneratedIdDemo />);
    // The point is that the summary does not depend on the shape of a
    // React-generated id - a format React has changed before (`:` in
    // React 18 was not a valid CSS selector; today it is `_r_0_`) and may
    // change again. getElementById is what makes that not matter.
    expect(screen.getByLabelText("Requesting term").id).not.toBe("");

    await user.click(screen.getByRole("link", { name: "Enter a requesting term" }));

    expect(screen.getByLabelText("Requesting term")).toHaveFocus();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <ErrorSummary errors={ERRORS} formError="The catalogue rejected this entry." />,
    );

    await expectNoA11yViolations(container);
  });
});
