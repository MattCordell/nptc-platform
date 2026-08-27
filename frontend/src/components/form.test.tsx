import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Button } from "./button.tsx";
import { Field } from "./field.tsx";
import { Form } from "./form.tsx";
import { RadioGroup } from "./radio-group.tsx";

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "published", label: "Published" },
];

/** A form that validates on submit, the way a real edit screen would. */
function ValidatingForm() {
  const [term, setTerm] = useState("");
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [errors, setErrors] = useState<{ fieldId: string; message: string }[]>([]);

  return (
    <Form
      submitLabel="Save entry"
      errors={errors}
      onSubmit={() => {
        setErrors(
          [
            term
              ? null
              : { fieldId: "requesting-term", message: "Enter a requesting term" },
            status ? null : { fieldId: "status", message: "Choose a status" },
          ].filter((error) => error !== null),
        );
      }}
    >
      <Field id="requesting-term" label="Requesting term">
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
          />
        )}
      </Field>
      <RadioGroup
        id="status"
        legend="Status"
        options={STATUS_OPTIONS}
        value={status}
        onChange={setStatus}
      />
    </Form>
  );
}

/** A form whose submit is answered asynchronously, with a server refusal. */
function AsyncRejectingForm({ onSubmit }: { onSubmit: () => void }) {
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | undefined>(undefined);

  return (
    <Form
      submitLabel="Save entry"
      pendingLabel="Saving"
      pending={pending}
      formError={formError}
      onSubmit={() => {
        onSubmit();
        setPending(true);
        window.setTimeout(() => {
          setPending(false);
          setFormError("The catalogue rejected this entry.");
        }, 0);
      }}
    >
      <Field id="requesting-term" label="Requesting term">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>
    </Form>
  );
}

/**
 * A form whose errors can be set from outside it, without a submit - the
 * case that separates "the answer to a submit" from "an error that simply
 * appeared".
 */
function ExternallyErroringForm() {
  const [errors, setErrors] = useState<{ fieldId: string; message: string }[]>([]);

  return (
    <>
      <button
        type="button"
        onClick={() =>
          setErrors([{ fieldId: "requesting-term", message: "Enter a requesting term" }])
        }
      >
        Set an error
      </button>
      <Form submitLabel="Save entry" errors={errors} onSubmit={() => setErrors([])}>
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>
    </>
  );
}

function summaryElement() {
  return screen.getByRole("heading", { name: "There is a problem" }).closest("div");
}

describe("Form", () => {
  it("submits once through its own submit button", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <Form submitLabel="Save entry" onSubmit={onSubmit}>
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>,
    );

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("submits on Enter from a text field, through the same single path", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <Form submitLabel="Save entry" onSubmit={onSubmit}>
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>,
    );

    await user.click(screen.getByLabelText("Requesting term"));
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("disables the submit button, swaps its label, and marks the form busy while pending", () => {
    render(
      <Form submitLabel="Save entry" pendingLabel="Saving" pending onSubmit={vi.fn()}>
        <p>Fields</p>
      </Form>,
    );

    expect(screen.getByRole("button", { name: "Saving" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Saving" }).closest("form"),
    ).toHaveAttribute("aria-busy", "true");
  });

  it("ignores a submit that arrives while one is already in flight", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <Form submitLabel="Save entry" pending onSubmit={onSubmit}>
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>,
    );

    // The button is disabled, so drive the form itself - the guard has to
    // hold for an Enter keypress too, not only for a click the disabled
    // button already refuses.
    await user.click(screen.getByLabelText("Requesting term"));
    await user.keyboard("{Enter}");

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows no summary until a submit fails", async () => {
    const user = userEvent.setup();
    render(<ValidatingForm />);

    expect(
      screen.queryByRole("heading", { name: "There is a problem" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(
      screen.getByRole("heading", { name: "There is a problem" }),
    ).toBeInTheDocument();
  });

  it("moves focus to the error summary when a submit fails validation", async () => {
    const user = userEvent.setup();
    render(<ValidatingForm />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(summaryElement()).toHaveFocus();
  });

  it("moves focus to the field a summary item names", async () => {
    const user = userEvent.setup();
    render(<ValidatingForm />);
    await user.click(screen.getByRole("button", { name: "Save entry" }));

    await user.click(screen.getByRole("link", { name: "Enter a requesting term" }));

    expect(screen.getByLabelText("Requesting term")).toHaveFocus();
  });

  it("sends a group's summary item to that group's first option, not to the fieldset", async () => {
    const user = userEvent.setup();
    render(<ValidatingForm />);
    await user.click(screen.getByRole("button", { name: "Save entry" }));

    await user.click(screen.getByRole("link", { name: "Choose a status" }));

    // Focusing a fieldset announces nothing useful; the first radio
    // announces the legend as its group, then the option.
    expect(screen.getByLabelText("Draft")).toHaveFocus();
  });

  it("does not steal focus when a later submit succeeds", async () => {
    const user = userEvent.setup();
    render(<ValidatingForm />);
    await user.click(screen.getByRole("button", { name: "Save entry" }));

    await user.type(screen.getByLabelText("Requesting term"), "Sodium");
    await user.click(screen.getByLabelText("Published"));
    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(
      screen.queryByRole("heading", { name: "There is a problem" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save entry" })).toHaveFocus();
  });

  it("focuses the summary when a server refusal arrives after the submit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AsyncRejectingForm onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText("The catalogue rejected this entry."),
    ).toBeInTheDocument();
    expect(summaryElement()).toHaveFocus();
  });

  it("renders secondary actions beside the submit button, and they do not submit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <Form
        submitLabel="Save entry"
        onSubmit={onSubmit}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        }
      >
        <p>Fields</p>
      </Form>,
    );

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("is operable by keyboard alone, from the first field to the submit button", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <Form submitLabel="Save entry" onSubmit={onSubmit}>
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>,
    );

    await user.tab();
    expect(screen.getByLabelText("Requesting term")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Save entry" })).toHaveFocus();
    await user.keyboard("{ }");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("leaves focus alone for an error that was not the answer to a submit", async () => {
    const user = userEvent.setup();
    render(<ExternallyErroringForm />);

    await user.click(screen.getByRole("button", { name: "Set an error" }));

    // The summary is on screen, but the user did not ask for it - moving
    // focus here would yank them out of whatever they were doing.
    expect(
      screen.getByRole("heading", { name: "There is a problem" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set an error" })).toHaveFocus();
  });

  it("stops waiting for a summary once a submit has been answered without errors", async () => {
    const user = userEvent.setup();
    render(<ExternallyErroringForm />);

    // A submit that produces no errors settles that submit; an error that
    // turns up afterwards belongs to something else and must not grab focus.
    await user.click(screen.getByRole("button", { name: "Save entry" }));
    await user.click(screen.getByRole("button", { name: "Set an error" }));

    expect(screen.getByRole("button", { name: "Set an error" })).toHaveFocus();
  });

  it("has no automated accessibility violations, with the summary showing", async () => {
    const user = userEvent.setup();
    const { container } = render(<ValidatingForm />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    await expectNoA11yViolations(container);
  });
});
