import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useKeysetPaging } from "./use-keyset-paging.ts";

/**
 * The "Previous page" memory shared by both catalogue list screens (ADR-0024).
 * The rule under test: the stack survives a change of `after` only when it is
 * the Next or Previous click that set the target.
 */

function render(initialAfter: string | undefined) {
  return renderHook(({ after }) => useKeysetPaging(after), {
    initialProps: { after: initialAfter },
  });
}

describe("useKeysetPaging", () => {
  it("starts with nothing to go back to", () => {
    const { result } = render(undefined);

    expect(result.current.hasPrevious).toBe(false);
  });

  it("goes back to the cursor each Next click left", () => {
    const { result, rerender } = render(undefined);

    act(() => result.current.next("c1"));
    rerender({ after: "c1" });
    act(() => result.current.next("c2"));
    rerender({ after: "c2" });
    expect(result.current.hasPrevious).toBe(true);

    let back: string | undefined;
    act(() => {
      back = result.current.previous();
    });
    expect(back).toBe("c1");
    rerender({ after: "c1" });
    expect(result.current.hasPrevious).toBe(true);

    act(() => {
      back = result.current.previous();
    });
    expect(back).toBeUndefined();
    rerender({ after: undefined });
    expect(result.current.hasPrevious).toBe(false);
  });

  // Browser Back and Forward, a new query or a new filter move `after` without
  // a click, so the stack no longer describes the route the screen holds.
  it("empties the stack when `after` changes by any other route", () => {
    const { result, rerender } = render(undefined);
    act(() => result.current.next("c1"));
    rerender({ after: "c1" });
    expect(result.current.hasPrevious).toBe(true);

    rerender({ after: "elsewhere" });

    expect(result.current.hasPrevious).toBe(false);
  });

  it("empties the stack when `after` is dropped without a Previous click", () => {
    const { result, rerender } = render(undefined);
    act(() => result.current.next("c1"));
    rerender({ after: "c1" });

    rerender({ after: undefined });

    expect(result.current.hasPrevious).toBe(false);
  });

  it("keeps a stack from a page opened straight from a link empty", () => {
    const { result } = render("from-a-link");

    expect(result.current.hasPrevious).toBe(false);
  });
});
