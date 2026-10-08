import type { components } from "../api/schema.ts";
import { StatusBadge } from "../components/status-badge.tsx";
import { EntrySection } from "./entry-section.tsx";
import { PropertyRows } from "./entry-property-rows.tsx";
import { formatDate } from "./format-date.ts";
import { statusLabelFor, statusToneFor } from "./status-options.ts";

type EntryDetail = components["schemas"]["EntryDetail"];

/**
 * The entry's own facts, beside the main column: identifier, status,
 * disciplines, every other property, and when it last changed. It shows no
 * term length (FR-85), no row version and no internal id.
 */
export function EntryMetadata({ entry }: { entry: EntryDetail }) {
  return (
    <EntrySection title="Details">
      <dl className="m-0 grid grid-cols-1 gap-y-1 text-sm">
        <dt className="font-medium">Identifier</dt>
        <dd className="m-0 mb-3 min-w-0 font-mono">{entry.business_key}</dd>

        <dt className="font-medium">Status</dt>
        <dd className="m-0 mb-3 min-w-0 last:mb-0">
          <StatusBadge
            tone={statusToneFor(entry.status)}
            label={statusLabelFor(entry.status)}
          />
        </dd>

        <dt className="font-medium">Disciplines</dt>
        <dd className="m-0 mb-3 min-w-0 last:mb-0">
          {entry.disciplines.length > 0 ? (
            entry.disciplines.join(", ")
          ) : (
            <span className="text-[var(--color-text-muted)]">None recorded</span>
          )}
        </dd>

        <PropertyRows properties={entry.properties} />

        <dt className="font-medium">Last updated</dt>
        <dd className="m-0 mb-3 min-w-0 last:mb-0">
          <time dateTime={entry.updated_at}>{formatDate(entry.updated_at)}</time>
        </dd>
      </dl>
    </EntrySection>
  );
}
