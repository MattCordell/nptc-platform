import type { components } from "../api/schema.ts";
import { CodeChip } from "../components/code-chip.tsx";
import { DataTable } from "../components/data-table.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { BindingRetirement } from "./binding-retirement.tsx";
import { bindingStatus } from "./binding-status.ts";
import { EntrySection, ScrollRegion } from "./entry-section.tsx";

type Binding = components["schemas"]["Binding"];

/** Active first, then every other status, each group in the order received. */
function activeFirst(bindings: Binding[]): Binding[] {
  return [
    ...bindings.filter((binding) => binding.status === "active"),
    ...bindings.filter((binding) => binding.status !== "active"),
  ];
}

/**
 * The SNOMED CT codes bound to the entry. A retired binding stays listed, after
 * the active one, with its reason and any replacement code (FR-08). The fully
 * specified name is shown exactly as served (FR-83).
 */
export function EntryBindings({ bindings }: { bindings: Binding[] }) {
  const rows = activeFirst(bindings);
  return (
    <EntrySection title="SNOMED CT codes">
      <ScrollRegion label="Code bindings">
        <DataTable
          caption="Code bindings"
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
              key: "au_preferred_term",
              header: "AU preferred term",
              render: (row: Binding) =>
                row.au_preferred_term ?? (
                  <span className="text-[var(--color-text-muted)]">None recorded</span>
                ),
            },
            {
              key: "status",
              header: "Status",
              render: (row: Binding) => {
                const { label, tone } = bindingStatus(row.status);
                return <StatusBadge tone={tone} label={label} />;
              },
            },
            {
              key: "retirement",
              header: "Retirement",
              render: (row: Binding) => <BindingRetirement binding={row} />,
            },
          ]}
          rows={rows}
          // `code` and `status` do not identify a row: a code can be bound,
          // retired, bound again and retired again, leaving two rows that
          // match on both. `Binding` has no id, so the position in this
          // render's `rows` disambiguates.
          getRowKey={(row) => `${row.code}:${row.status}:${rows.indexOf(row)}`}
          emptyState="This entry has no SNOMED CT code."
        />
      </ScrollRegion>
    </EntrySection>
  );
}
