import { useEntryHistory } from "../api/queries.ts";
import { EntrySection } from "./entry-section.tsx";
import { formatDate } from "./format-date.ts";
import { describeChange } from "./history-text.ts";

/** Enough to show what changed lately without turning the sidebar into the
 * full history page. */
const RECENT_EVENTS = 5;

/**
 * The entry's most recent changes (FR-19). It has its own query, so a slow or
 * failed history never holds back the rest of the page. The author appears only
 * when the API sends one: it sends none to an anonymous reader (NFR-26).
 */
export function EntryHistoryList({ businessKey }: { businessKey: string }) {
  const history = useEntryHistory(businessKey, { limit: RECENT_EVENTS });

  return (
    <EntrySection title="Recent changes">
      {history.isPending ? (
        <p role="status" className="m-0 text-[var(--color-text-muted)]">
          Loading recent changes…
        </p>
      ) : history.isError ? (
        <p role="status" className="m-0">
          The recent changes could not be loaded. The rest of this entry is unaffected.
        </p>
      ) : history.data.items.length === 0 ? (
        <p className="m-0 text-[var(--color-text-muted)]">No changes are recorded.</p>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-3 p-0 text-sm">
          {history.data.items.map((event, index) => {
            const { action, fields } = describeChange(event);
            return (
              <li key={`${event.occurred_at}:${index}`} className="flex flex-col gap-0.5">
                <time dateTime={event.occurred_at} className="font-medium">
                  {formatDate(event.occurred_at)}
                </time>
                <span>{action}</span>
                {fields !== null ? (
                  <span className="text-[var(--color-text-muted)]">Fields: {fields}</span>
                ) : null}
                {event.note !== null && event.note !== "" ? (
                  <span className="text-[var(--color-text-muted)]">{event.note}</span>
                ) : null}
                {event.changed_by !== null ? (
                  <span className="text-[var(--color-text-muted)]">
                    By {event.changed_by}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </EntrySection>
  );
}
