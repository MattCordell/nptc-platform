import type { components } from "../api/schema.ts";
import { DataTable } from "../components/data-table.tsx";
import { EntrySection, ScrollRegion } from "./entry-section.tsx";

type EntryDetail = components["schemas"]["EntryDetail"];

interface TermRow {
  type: string;
  term: string;
}

/** The rows in reading order: the RCPA terms, then the SNOMED CT ones. */
function termRows(entry: EntryDetail): TermRow[] {
  const rows: TermRow[] = [{ type: "RCPA Preferred", term: entry.preferred_term }];
  for (const designation of entry.designations) {
    rows.push({ type: "RCPA Synonym", term: designation.term });
  }

  // A served FSN keeps its semantic tag (FR-82, FR-83).
  const active = entry.bindings.find((binding) => binding.status === "active");
  if (active !== undefined) {
    rows.push({ type: "SNOMED CT FSN", term: active.fsn });
    if (active.au_preferred_term !== null) {
      rows.push({ type: "SNOMED CT Preferred", term: active.au_preferred_term });
    }
  }
  return rows;
}

/**
 * Every name the entry goes by, in one table: the RCPA terms and the active
 * SNOMED CT binding's names. An entry with no active binding shows the RCPA
 * rows alone.
 */
export function EntryTerms({ entry }: { entry: EntryDetail }) {
  const rows = termRows(entry);
  return (
    <EntrySection title="Terms">
      <ScrollRegion label="Terms by type">
        <DataTable
          caption="Terms by type"
          columns={[
            {
              key: "term",
              header: "Term",
              isRowHeader: true,
              render: (row: TermRow) => row.term,
            },
            {
              key: "type",
              header: "Type",
              render: (row: TermRow) => row.type,
            },
          ]}
          rows={rows}
          getRowKey={(row) => `${row.type}:${row.term}`}
          emptyState="This entry has no terms."
        />
      </ScrollRegion>
    </EntrySection>
  );
}
