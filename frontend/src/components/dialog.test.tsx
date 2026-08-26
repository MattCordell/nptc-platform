import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Button } from "./button.tsx";
import { Dialog } from "./dialog.tsx";

function DialogDemo({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          onClose?.();
        }}
        title="Confirm delete"
      >
        <p>This cannot be undone.</p>
        <Button type="button" onClick={() => setOpen(false)}>
          Confirm
        </Button>
      </Dialog>
    </div>
  );
}

function EmptyDialogDemo() {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Loading">
        <p>Please wait.</p>
      </Dialog>
    </div>
  );
}

function UnmountingDialogDemo() {
  const [show, setShow] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setShow(true)}>
        Open dialog
      </Button>
      {show ? (
        <Dialog open onClose={() => setShow(false)} title="Confirm delete">
          <Button type="button" onClick={() => setShow(false)}>
            Confirm
          </Button>
        </Dialog>
      ) : null}
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

  it("calls onClose exactly once for a single Escape press", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DialogDemo onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose exactly once for a native cancel event", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DialogDemo onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    screen.getByRole("dialog").dispatchEvent(new Event("cancel", { cancelable: true }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("is still closable by keyboard when it has no focusable descendants", async () => {
    const user = userEvent.setup();
    render(<EmptyDialogDemo />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(screen.getByRole("dialog", { name: "Loading" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("restores focus to the trigger when unmounted while still open", async () => {
    const user = userEvent.setup();
    render(<UnmountingDialogDemo />);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Confirm delete" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
