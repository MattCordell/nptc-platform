import type { components } from "../api/schema.ts";
import { CodeChip } from "../components/code-chip.tsx";
import { DataTable } from "../components/data-table.tsx";
import { StatusBadge, type StatusTone } from "../components/status-badge.tsx";
import { EntrySection } from "./entry-section.tsx";

type Binding = components["schemas"]["Binding"];

/** An unlisted status shows as its raw text on a neutral pill, not a failure. */
const BINDING_STATUSES: Record<string, { label: string; tone: StatusTone }> = {
  active: { label: "Active", tone: "active" },
  retired: { label: "Retired", tone: "deprecated" },
};

function bindingStatus(status: string): { label: string; tone: StatusTone } {
  return BINDING_STATUSES[status] ?? { label: status, tone: "neutral" };
}

/** Active first, then every other status, each group in the order received. */
function activeFirst(bindings: Binding[]): Binding[] {
  return [
    ...bindings.filter((binding) => binding.status === "active"),
    ...bindings.filter((binding) => binding.status !== "active"),
  ];
}

function Retirement({ binding }: { binding: Binding }) {
  if (binding.retirement_reason === null && binding.replaced_by_code === null) {
    return <span className="text-[var(--color-text-muted)]">—</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      {binding.retirement_reason !== null ? (
        <span>{binding.retirement_reason}</span>
      ) : null}
      {binding.replaced_by_code !== null ? (
        <span>
          Replaced by <CodeChip code={binding.replaced_by_code} />
        </span>
      ) : null}
    </div>
  );
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
      <div className="overflow-x-auto">
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
              render: (row: Binding) => <Retirement binding={row} />,
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
      </div>
    </EntrySection>
  );
}
