import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { Form } from "../components/form.tsx";

function TestForm({ onSubmit }: { onSubmit: () => void }) {
  const changelogNote = useChangelogNote("test-note");
  return (
    <Form
      submitLabel="Save"
      submitBlocked={changelogNote.blocked}
      blockedReason={changelogNote.blockedReason}
      blockedFieldId={changelogNote.fieldId}
      onSubmit={onSubmit}
    >
      <ChangelogNoteField id="test-note" changelogNote={changelogNote} />
    </Form>
  );
}

describe("ChangelogNoteField", () => {
  it("gates submit until a valid note is entered", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<TestForm onSubmit={onSubmit} />);

    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    await user.type(
      screen.getByLabelText("Changelog note"),
      "Corrected the specimen for the RBC assay",
    );

    expect(screen.getByRole("button", { name: "Save" })).not.toHaveAttribute(
      "aria-disabled",
    );

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("shows no guidance before the field has been touched", () => {
    render(<TestForm onSubmit={vi.fn()} />);

    expect(screen.queryByText(/describe what actually changed/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Changelog note")).not.toHaveAttribute("aria-invalid");
  });

  it("shows the low-information guidance once the field is blurred, before submit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<TestForm onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("Changelog note"), "fix");
    await user.tab();

    expect(await screen.findByText(/describe what actually changed/)).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("refuses submit and announces the reason for a low-information note", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<TestForm onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("Changelog note"), "fix");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).not.toHaveBeenCalled();
    // Both the field's own inline guidance and the summary's blockedReason
    // carry the message once a blocked submit is attempted.
    expect(screen.getAllByText(/describe what actually changed/)).toHaveLength(2);
  });

  it("has no automated accessibility violations, gated and ungated", async () => {
    const user = userEvent.setup();
    const { container } = render(<TestForm onSubmit={vi.fn()} />);

    await expectNoA11yViolations(container);

    await user.type(
      screen.getByLabelText("Changelog note"),
      "Corrected the specimen for the RBC assay",
    );

    await expectNoA11yViolations(container);
  });
});
