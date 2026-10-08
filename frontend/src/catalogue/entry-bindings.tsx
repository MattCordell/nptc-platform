import type { components } from "../api/schema.ts";
import { CodeChip } from "../components/code-chip.tsx";
import { DataTable } from "../components/data-table.tsx";
import { BindingRetirement } from "./binding-retirement.tsx";
import { EntrySection, ScrollRegion } from "./entry-section.tsx";

type Binding = components["schemas"]["Binding"];

/**
 * The entry's retired SNOMED CT bindings, each with its reason and any
 * replacement code (FR-08). The active binding's names are rows of the Terms
 * card, so this card is absent when nothing is retired. How retired bindings
 * should finally appear is not yet decided; this is a placeholder.
 */
export function EntryBindings({ bindings }: { bindings: Binding[] }) {
  const rows = bindings.filter((binding) => binding.status === "retired");
  if (rows.length === 0) {
    return null;
  }
  return (
    <EntrySection title="Retired SNOMED CT codes">
      <ScrollRegion label="Retired code bindings">
        <DataTable
          caption="Retired code bindings"
          columns={[
            {
              key: "code",
              header: "Code",
              isRowHeader: true,
              render: (row: Binding) => <CodeChip code={row.code} />,
            },
            {
              key: "fsn",
              header: "Fully specified name",
              render: (row: Binding) => row.fsn,
            },
            {
              key: "retirement",
              header: "Retirement",
              render: (row: Binding) => <BindingRetirement binding={row} />,
            },
          ]}
          rows={rows}
          // A code can be bound, retired, bound again and retired again, so
          // `code` does not identify a row. `Binding` has no id, so the
          // position in this render's `rows` disambiguates.
          getRowKey={(row) => `${row.code}:${rows.indexOf(row)}`}
          emptyState="This entry has no retired SNOMED CT codes."
        />
      </ScrollRegion>
    </EntrySection>
  );
}
