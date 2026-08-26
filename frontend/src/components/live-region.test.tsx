import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { LiveRegion } from "./live-region.tsx";
import { useAnnounce } from "./use-announce.ts";

function AsyncResultDemo() {
  const { message, politeness, announce } = useAnnounce();

  return (
    <div>
      <button
        type="button"
        onClick={async () => {
          await Promise.resolve(); // stand in for an async save/search
          announce("Saved successfully");
        }}
      >
        Save
      </button>
      <LiveRegion message={message} politeness={politeness} />
    </div>
  );
}

describe("LiveRegion", () => {
  it("is present and empty on mount, not inserted only when a message arrives", () => {
    render(<LiveRegion message="" />);
    const region = screen.getByRole("status");
    expect(region).toBeInTheDocument();
    expect(region).toHaveTextContent("");
  });

  it("defaults to polite, with assertive available as an opt-in", () => {
    const { rerender } = render(<LiveRegion message="" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");

    rerender(<LiveRegion message="" politeness="assertive" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "assertive");
  });

  it("announces an async result through the region", async () => {
    render(<AsyncResultDemo />);

    await act(async () => {
      screen.getByRole("button", { name: "Save" }).click();
    });

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Saved successfully"),
    );
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<LiveRegion message="Loaded" />);
    await expectNoA11yViolations(container);
  });
});
