import { useState } from "react";

type PagingState = {
  stack: (string | undefined)[];
  seen: string | undefined;
  target: { after: string | undefined } | null;
};

/**
 * The "Previous page" memory for a keyset-paged list (ADR-0024). The server
 * returns no previous cursor, so this keeps the cursors of the pages already
 * visited, and `previous()` returns the one to go back to.
 *
 * The stack survives a change of `after` only when that change is the Next or
 * Previous click that set the target. Any other change (a new query, filter or
 * sort drops `after`; Back or Forward moves it) leaves a stack describing a
 * route the screen no longer holds, so it is emptied.
 */
export function useKeysetPaging(after: string | undefined) {
  const [paging, setPaging] = useState<PagingState>({
    stack: [],
    seen: after,
    target: null,
  });
  if (after !== paging.seen) {
    setPaging({
      stack: paging.target !== null && paging.target.after === after ? paging.stack : [],
      seen: after,
      target: null,
    });
  }

  return {
    hasPrevious: paging.stack.length > 0,
    /** Records that the screen is about to move on to `cursor`. */
    next(cursor: string): void {
      setPaging({
        ...paging,
        stack: [...paging.stack, after],
        target: { after: cursor },
      });
    },
    /** Records the step back and returns the `after` to navigate to. */
    previous(): string | undefined {
      const previousAfter = paging.stack[paging.stack.length - 1];
      setPaging({
        ...paging,
        stack: paging.stack.slice(0, -1),
        target: { after: previousAfter },
      });
      return previousAfter;
    },
  };
}
