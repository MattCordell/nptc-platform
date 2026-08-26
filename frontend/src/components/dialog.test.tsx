import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Button } from "./button.tsx";
import { Dialog } from "./dialog.tsx";

function DialogDemo() {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Confirm delete">
        <p>This cannot be undone.</p>
        <Button type="button" onClick={() => setOpen(false)}>
          Confirm
        </Button>
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("moves focus into the dialog when opened", async () => {
    const user = userEvent.setup();
    render(<DialogDemo />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));

    expect(screen.getByRole("dialog", { name: "Confirm delete" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  });

  it("restores focus to the trigger when closed via Escape", async () => {
    const user = userEvent.setup();
    render(<DialogDemo />);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Confirm delete" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("restores focus to the trigger when closed via its own control", async () => {
    const user = userEvent.setup();
    render(<DialogDemo />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open dialog" })).toHaveFocus();
  });

  it("traps Tab within the dialog", async () => {
    const user = userEvent.setup();
    render(<DialogDemo />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    const confirmButton = screen.getByRole("button", { name: "Confirm" });

    await user.tab();
    expect(confirmButton).toHaveFocus();

    // With only one focusable element inside the dialog besides itself,
    // tabbing again must cycle back within the dialog, not escape to
    // "Open dialog" which sits outside it in the DOM order.
    await user.tab();
    expect(screen.getByRole("button", { name: "Open dialog" })).not.toHaveFocus();
  });

  it("has no automated accessibility violations while open", async () => {
    const user = userEvent.setup();
    const { container } = render(<DialogDemo />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));

    await expectNoA11yViolations(container);
  });
});
