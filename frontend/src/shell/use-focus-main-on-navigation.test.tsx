import { render, screen } from "@testing-library/react";
import { useRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useFocusMainOnNavigation } from "./use-focus-main-on-navigation.ts";

let pathname = "/first";

// The hook's only router input is the pathname, so it is driven directly:
// that makes the commit order between a page's own focus move and this hook
// a fixed input instead of something the real router's timing decides.
vi.mock("@tanstack/react-router", () => ({
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => string;
  }) => select({ location: { pathname } }),
}));

function Harness({ focusInside }: { focusInside: boolean }) {
  const mainRef = useRef<HTMLElement>(null);
  useFocusMainOnNavigation(mainRef);
  return (
    <>
      <button type="button">Outside</button>
      <main ref={mainRef} tabIndex={-1}>
        <button
          type="button"
          ref={(node) => {
            if (focusInside) {
              node?.focus();
            }
          }}
        >
          Inside
        </button>
      </main>
    </>
  );
}

describe("useFocusMainOnNavigation", () => {
  beforeEach(() => {
    pathname = "/first";
  });

  it("moves focus to <main> when the path changes and focus is elsewhere", () => {
    const { rerender } = render(<Harness focusInside={false} />);
    screen.getByRole("button", { name: "Outside" }).focus();

    pathname = "/second";
    rerender(<Harness focusInside={false} />);

    expect(document.activeElement).toBe(screen.getByRole("main"));
  });

  it("leaves focus where it is when it is already inside <main>", () => {
    const { rerender } = render(<Harness focusInside />);
    const inside = screen.getByRole("button", { name: "Inside" });
    expect(document.activeElement).toBe(inside);

    pathname = "/second";
    rerender(<Harness focusInside />);

    expect(document.activeElement).toBe(inside);
  });

  it("does not move focus when the path is unchanged", () => {
    const { rerender } = render(<Harness focusInside={false} />);
    screen.getByRole("button", { name: "Outside" }).focus();

    rerender(<Harness focusInside={false} />);

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Outside" }));
  });
});
