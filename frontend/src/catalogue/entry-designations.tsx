import type { components } from "../api/schema.ts";
import { DataTable } from "../components/data-table.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { EntrySection, ScrollRegion } from "./entry-section.tsx";
import { statusLabelFor, statusToneFor } from "./status-options.ts";

type Designation = components["schemas"]["Designation"];

/** An unlisted `use` shows as the raw value rather than failing. */
const USE_LABELS: Record<string, string> = {
  synonym: "Synonym",
  preferred: "Preferred term in another language",
};

/**
 * The entry's other terms: synonyms, and preferred terms in languages other
 * than en-AU. The en-AU preferred term is the page's heading, not a row here.
 */
export function EntryDesignations({ designations }: { designations: Designation[] }) {
  return (
    <EntrySection title="Terms">
      <ScrollRegion label="Synonyms and other-language terms">
        <DataTable
          caption="Synonyms and other-language terms"
          columns={[
            {
              key: "term",
              header: "Term",
              isRowHeader: true,
              render: (row: Designation) => row.term,
            },
            {
              key: "use",
              header: "Type",
              render: (row: Designation) => USE_LABELS[row.use] ?? row.use,
            },
            {
              key: "language",
              header: "Language",
              render: (row: Designation) => row.language,
            },
            {
              key: "status",
              header: "Status",
              render: (row: Designation) => (
                <StatusBadge
                  tone={statusToneFor(row.status)}
                  label={statusLabelFor(row.status)}
                />
              ),
            },
          ]}
          rows={designations}
          getRowKey={(row) => `${row.use}:${row.language}:${row.term}`}
          emptyState="This entry has no synonyms or other-language terms."
        />
      </ScrollRegion>
    </EntrySection>
  );
}
