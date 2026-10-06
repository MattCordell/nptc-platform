import { useId } from "react";
import type { ComponentPropsWithoutRef } from "react";

import { Button } from "./button.tsx";

type PaginationProps = {
  /** Whether a following page exists: `next_cursor !== null`. */
  hasNext: boolean;
  onNext: () => void;
  /** Left off when there is no earlier page to return to. The backend returns
   * no previous cursor (ADR-0024), so how to go back is the caller's call. */
  onPrevious?: () => void;
  /** The landmark's accessible name; set it when a page has more than one. */
  label?: string;
} & Omit<ComponentPropsWithoutRef<"nav">, "children" | "aria-label">;

/**
 * Next and Previous controls for keyset paging (FR-14, FR-16): no page
 * numbers and no total, because neither exists. Names no URL parameter, so a
 * screen paging by `after` and one paging by `before` both use it.
 *
 * Both buttons stay in the tab order when unavailable (`aria-disabled`, not
 * `disabled`), so focus is not stranded when the last page loads under a
 * keyboard user. The click guard below is therefore needed: `aria-disabled`
 * alone does not stop the event.
 */
export function Pagination({
  hasNext,
  onNext,
  onPrevious,
  label = "Pagination",
  className,
  ...rest
}: PaginationProps) {
  const endMessageId = useId();

  return (
    <nav
      {...rest}
      aria-label={label}
      className={["flex flex-wrap items-center gap-3", className ?? ""]
        .filter(Boolean)
        .join(" ")}
    >
      <Button
        type="button"
        variant="secondary"
        className="min-h-10"
        aria-disabled={onPrevious === undefined}
        onClick={() => onPrevious?.()}
      >
        Previous page
      </Button>
      <Button
        type="button"
        variant="secondary"
        className="min-h-10"
        aria-disabled={!hasNext}
        aria-describedby={hasNext ? undefined : endMessageId}
        onClick={() => {
          if (hasNext) {
            onNext();
          }
        }}
      >
        Next page
      </Button>
      {hasNext ? null : (
        <p id={endMessageId} className="text-sm text-[var(--color-text-muted)]">
          No more results
        </p>
      )}
    </nav>
  );
}
