import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function RepeatAnnounceDemo() {
  const { message, announce } = useAnnounce();

  return (
    <div>
      <button type="button" onClick={() => announce("No results found")}>
        Search
      </button>
      <LiveRegion message={message} />
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

  it("defaults to a polite status role, with an assertive alert role as an opt-in", () => {
    const { rerender } = render(<LiveRegion message="" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(<LiveRegion message="" politeness="assertive" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
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

  describe("repeat announcements", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("clears the region before re-announcing the same message twice in a row", () => {
      render(<RepeatAnnounceDemo />);
      const searchButton = screen.getByRole("button", { name: "Search" });

      fireEvent.click(searchButton);
      act(() => vi.runAllTimers());
      expect(screen.getByRole("status")).toHaveTextContent("No results found");

      fireEvent.click(searchButton);
      // Immediately after the second click, before the deferred re-set
      // fires, the region must already be empty - clearing and re-setting
      // in the same microtask/commit would leave the DOM text unchanged,
      // and some screen readers only announce on a text *change*.
      expect(screen.getByRole("status")).toBeEmptyDOMElement();

      act(() => vi.runAllTimers());
      expect(screen.getByRole("status")).toHaveTextContent("No results found");
    });
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<LiveRegion message="Loaded" />);
    await expectNoA11yViolations(container);
  });
});
