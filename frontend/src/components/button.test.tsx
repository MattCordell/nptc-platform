import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { Button } from "./button.tsx";

describe("Button", () => {
  it("requires an explicit type, so it never defaults to submit inside a form", () => {
    render(<Button type="button">Cancel</Button>);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute(
      "type",
      "button",
    );
  });

  it("is operable by keyboard alone", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button type="button" onClick={onClick}>
        Save
      </Button>,
    );

    await user.tab();
    expect(screen.getByRole("button", { name: "Save" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is not focusable, and not clickable, when disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button type="button" onClick={onClick} disabled>
        Save
      </Button>,
    );

    await user.tab();
    expect(screen.getByRole("button", { name: "Save" })).not.toHaveFocus();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("styles an aria-disabled button as unavailable, while keeping it focusable", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button type="submit" aria-disabled onClick={onClick}>
        Saving
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Saving" });
    expect(button.className).toContain("opacity-50");
    expect(button.className).toContain("cursor-not-allowed");
    expect(button.className).not.toContain("cursor-pointer");

    // The point of aria-disabled over disabled: still in the tab order, so
    // a keyboard user is not stranded when it turns unavailable under them.
    // Refusing the action is the caller's job, not the styling's.
    await user.tab();
    expect(button).toHaveFocus();
    expect(button).not.toBeDisabled();
  });

  it.each(["primary", "secondary", "danger"] as const)(
    "has no automated accessibility violations for the %s variant",
    async (variant) => {
      const { container } = render(
        <Button type="button" variant={variant}>
          Action
        </Button>,
      );
      await expectNoA11yViolations(container);
    },
  );
});
