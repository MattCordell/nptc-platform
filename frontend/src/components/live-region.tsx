import type { Politeness } from "./use-announce.ts";

/**
 * A single async announcement, always rendered - present and empty on
 * mount, not inserted into the DOM only at announce time. A screen reader
 * reliably picks up a text change inside a region that was already present
 * when the page loaded; an element created and populated in the same tick
 * is not guaranteed to be (issue #148's live-region baseline component).
 *
 * Pairs with `useAnnounce` (`use-announce.ts`), which owns the message and
 * politeness state a screen sets when an async result arrives.
 */
export function LiveRegion({
  message,
  politeness = "polite",
}: {
  message: string;
  politeness?: Politeness;
}) {
  return (
    <div
      role="status"
      aria-live={politeness}
      aria-atomic="true"
      className="visually-hidden"
    >
      {message}
    </div>
  );
}
