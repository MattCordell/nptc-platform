import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { buttonClassName } from "./button-class-name.ts";
import { Button } from "./button.tsx";

describe("buttonClassName", () => {
  // Asserted against the literal string, not against what `Button` itself
  // renders: `Button` calls `buttonClassName` internally, so comparing the
  // two would just assert the function against itself and could never catch
  // a drift - which is exactly what this export exists to prevent.
  it("returns the base classes plus the secondary variant's colours", () => {
    expect(buttonClassName("secondary")).toBe(
      "rounded-md border px-4 py-2 text-sm font-medium bg-[var(--color-surface)] text-[var(--color-text)] border-[var(--color-border)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]",
    );
  });

  it("defaults to the primary variant", () => {
    expect(buttonClassName()).toBe(buttonClassName("primary"));
  });
});

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

  // These two assert the Tailwind class contract, not rendered style - jsdom
  // has no layout engine, so there is no hover state or computed colour to
  // check (PR #327 review). They are change-detectors for the class string
  // above them, not visual proof the hover state renders correctly.
  it("fills the accent-hover colour on hover for the primary variant", () => {
    render(
      <Button type="button" variant="primary">
        Save
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Save" }).className).toContain(
      "hover:bg-[var(--color-accent-hover)]",
    );
  });

  it("takes the accent border and text on hover for the secondary variant", () => {
    render(
      <Button type="button" variant="secondary">
        Cancel
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Cancel" });
    expect(button.className).toContain("hover:border-[var(--color-accent)]");
    expect(button.className).toContain("hover:text-[var(--color-accent)]");
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
