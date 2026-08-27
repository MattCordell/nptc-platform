import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";
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

/**
 * A form whose `errors` array never changes identity, the way a memoised or
 * module-level one would not.
 */
const STABLE_ERRORS = [
  { fieldId: "requesting-term", message: "Enter a requesting term" },
];

function StableErrorsForm({ onSubmit }: { onSubmit: () => void }) {
  return (
    <Form submitLabel="Save entry" errors={STABLE_ERRORS} onSubmit={onSubmit}>
      <Field id="requesting-term" label="Requesting term">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>
    </Form>
  );
}

/**
 * A form whose caller never sets `pending` at all and whose refusal arrives
 * several renders after the submit - the shape of a mutation hook that
 * flips its own state a tick later. The focus contract must not depend on
 * `pending` having flipped on the very next render.
 */
function SlowRefusingForm() {
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (tick === 0 || tick > 3) {
      return;
    }
    const id = window.setTimeout(() => {
      if (tick === 3) {
        setFormError("The catalogue rejected this entry.");
      }
      setTick((current) => current + 1);
    }, 0);
    return () => window.clearTimeout(id);
  }, [tick]);

  return (
    <Form submitLabel="Save entry" formError={formError} onSubmit={() => setTick(1)}>
      <Field id="requesting-term" label="Requesting term">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>
    </Form>
  );
}

/** A form whose save starts and never finishes, so the mid-save state can
 *  actually be observed. */
function NeverFinishingForm() {
  const [pending, setPending] = useState(false);

  return (
    <Form
      submitLabel="Save entry"
      pendingLabel="Saving"
      pending={pending}
      onSubmit={() => setPending(true)}
    >
      <Field id="requesting-term" label="Requesting term">
        {(controlProps) => <input {...controlProps} type="text" />}
      </Field>
    </Form>
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

    const button = screen.getByRole("button", { name: "Saving" });
    // aria-disabled, not disabled: a disabled control leaves the tab order
    // and drops focus to <body> mid-save. See form.tsx.
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toBeDisabled();
    expect(button.closest("form")).toHaveAttribute("aria-busy", "true");
  });

  it("keeps focus on the submit button when a save starts, rather than stranding it", async () => {
    const user = userEvent.setup();
    render(<NeverFinishingForm />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    // The label has swapped, so this is the same button mid-save. A
    // `disabled` attribute here would have moved focus to <body>.
    expect(screen.getByRole("button", { name: "Saving" })).toHaveFocus();
  });

  it("refuses a click on the aria-disabled submit button while a save is in flight", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <Form submitLabel="Save entry" pending onSubmit={onSubmit}>
        <p>Fields</p>
      </Form>,
    );

    // The button is still clickable - that is the point of aria-disabled -
    // so the guard in the submit handler is what has to refuse it.
    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not let a caller unpick noValidate or aria-busy through spread props", () => {
    render(
      <Form
        submitLabel="Save entry"
        pending
        onSubmit={vi.fn()}
        noValidate={false}
        aria-busy={false}
      >
        <p>Fields</p>
      </Form>,
    );

    const form = screen.getByRole("button", { name: "Save entry" }).closest("form");
    expect(form).toHaveAttribute("novalidate");
    expect(form).toHaveAttribute("aria-busy", "true");
  });

  it("gives the summary the heading level its surroundings need", async () => {
    const user = userEvent.setup();
    render(
      <Form
        submitLabel="Save entry"
        errors={STABLE_ERRORS}
        errorSummaryHeadingLevel={3}
        onSubmit={vi.fn()}
      >
        <Field id="requesting-term" label="Requesting term">
          {(controlProps) => <input {...controlProps} type="text" />}
        </Field>
      </Form>,
    );

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(
      screen.getByRole("heading", { level: 3, name: "There is a problem" }),
    ).toBeInTheDocument();
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

  it("keeps listening for the answer to a submit until an error actually arrives", async () => {
    const user = userEvent.setup();
    render(<ExternallyErroringForm />);

    // The deliberate cost of not consulting `pending`: a submit leaves the
    // form listening, so an error that turns up afterwards is treated as
    // that submit's answer and announced. After a submit, an error is far
    // more likely to be its answer than not - and the case that matters
    // more, a refusal arriving several renders later, is announced at all.
    await user.click(screen.getByRole("button", { name: "Save entry" }));
    await user.click(screen.getByRole("button", { name: "Set an error" }));

    expect(summaryElement()).toHaveFocus();
  });

  it("announces a refusal that arrives late, from a caller that never sets pending", async () => {
    const user = userEvent.setup();
    render(<SlowRefusingForm />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    expect(
      await screen.findByText("The catalogue rejected this entry."),
    ).toBeInTheDocument();
    expect(summaryElement()).toHaveFocus();
  });

  it("announces again on a resubmit that fails the same way", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StableErrorsForm onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));
    expect(summaryElement()).toHaveFocus();

    // Move away, then submit again. The errors are the same array, so
    // nothing about them changed - but the user asked a second time and is
    // owed the answer a second time.
    await user.click(screen.getByLabelText("Requesting term"));
    expect(summaryElement()).not.toHaveFocus();

    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(summaryElement()).toHaveFocus();
  });

  it("has no automated accessibility violations, with the summary showing", async () => {
    const user = userEvent.setup();
    const { container } = render(<ValidatingForm />);

    await user.click(screen.getByRole("button", { name: "Save entry" }));

    await expectNoA11yViolations(container);
  });
});
