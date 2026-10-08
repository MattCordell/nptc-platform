import { formatEventTime } from "../audit/audit-filters.ts";
import type { components } from "../api/schema.ts";
import { describeChange } from "./history-text.ts";

type HistoryEvent = components["schemas"]["HistoryEvent"];

/**
 * An entry's changes as a version history (FR-19): a rule down the left, and for
 * each change its time and author above what happened. The author is left out
 * when the API sends none, which it does to an anonymous reader, for a system
 * change and for a closed account alike (NFR-26), so the list never claims
 * which of those it was.
 *
 * The API gives an event no identifier, so the key is its time and position.
 */
export function EntryAuditTrail({ events }: { events: HistoryEvent[] }) {
  return (
    <ol
      aria-label="Changes to this entry, newest first"
      className="m-0 flex list-none flex-col gap-5 border-l border-[var(--color-border)] p-0 pl-5"
    >
      {events.map((event, index) => {
        const { action, fields } = describeChange(event);
        return (
          <li key={`${event.occurred_at}:${index}`} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <time
                dateTime={event.occurred_at}
                className="font-mono text-sm whitespace-nowrap tabular-nums"
              >
                {formatEventTime(event.occurred_at)}
              </time>
              {event.changed_by !== null ? (
                <span className="text-sm text-[var(--color-text-muted)]">
                  By {event.changed_by}
                </span>
              ) : null}
            </div>
            <span>{action}</span>
            {fields !== null ? (
              <span className="text-sm text-[var(--color-text-muted)]">
                Fields: {fields}
              </span>
            ) : null}
            {event.note !== null && event.note !== "" ? (
              <span className="text-sm text-[var(--color-text-muted)]">
                Reason: {event.note}
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
