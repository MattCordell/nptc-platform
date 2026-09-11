import type { ReactNode } from "react";

import { Checkbox } from "./checkbox.tsx";

type Column<Row> = {
  key: string;
  header: string;
  /** Marks this column as the row header (`<th scope="row">`) rather than
   * a data cell - typically the row's identifying field. At most one
   * column should set this. */
  isRowHeader?: boolean;
  /** Text alignment for both the header cell and the matching data cell.
   * Defaults to left. */
  align?: "left" | "right";
  render: (row: Row) => ReactNode;
};

type SelectionProps<Row> = {
  /** Keys of the currently selected rows, per `getRowKey` - a `Set` rather
   * than a predicate, so the caller (not this component) owns what
   * "selected" means across a data change (issue #267). */
  selectedKeys: ReadonlySet<string>;
  onSelectRow: (key: string, selected: boolean) => void;
  onSelectAll: (selected: boolean) => void;
  /** The select-all box's accessible label, e.g. "Select all rows". */
  selectAllLabel: string;
  /** One row's box accessible label, e.g. `` `Select ${row.businessKey}` ``
   * - a bare checkbox in a cell is unlabelled to a screen reader, and the
   * row's own identifying text is usually already rendered in another
   * column, so this label is visually hidden rather than shown twice. */
  getRowLabel: (row: Row) => string;
};

type DataTableProps<Row> = {
  /** Rendered as the table's `<caption>` - every table has one, there is
   * no unlabelled fallback, matching `Dialog`'s required `title`. */
  caption: string;
  columns: Column<Row>[];
  rows: Row[];
  getRowKey: (row: Row) => string;
  /** Shown as a single full-width cell in place of the row set when
   * `rows` is empty, so "there is nothing here" is itself conveyed to
   * assistive technology rather than the table silently disappearing. */
  emptyState: ReactNode;
  /** Adds a leading selection column with a per-row box and a select-all
   * box in the header (issue #267). Omitted entirely (not merely unused)
   * for every existing caller - `Column` and every other prop are
   * untouched, so this is additive. */
  selection?: SelectionProps<Row>;
};

/**
 * A data table with a required caption, correctly-scoped column and row
 * headers, and an explicit empty state (issue #148). `scope="col"`/
 * `scope="row"` are what let a screen reader announce "column: <header>"
 * or "row: <header>" as a user navigates cell by cell - without them a
 * table reads as an undifferentiated grid of text.
 */
export function DataTable<Row>({
  caption,
  columns,
  rows,
  getRowKey,
  emptyState,
  selection,
}: DataTableProps<Row>) {
  // Only the first `isRowHeader` column, if any, is actually treated as the
  // row header - a caller declaring two would otherwise produce two
  // <th scope="row"> per row, breaking the single-row-header association
  // this component exists to guarantee. Deterministic rather than thrown:
  // a rendering component degrading to "first one wins" is preferable to
  // crashing a screen over a caller mistake in a column list.
  const rowHeaderKey = columns.find((column) => column.isRowHeader)?.key;
  const columnCount = columns.length + (selection ? 1 : 0);

  const selectedCount = selection
    ? rows.filter((row) => selection.selectedKeys.has(getRowKey(row))).length
    : 0;
  const allSelected =
    selection !== undefined && rows.length > 0 && selectedCount === rows.length;
  const someSelected = selectedCount > 0 && !allSelected;

  return (
    <table className="w-full border-collapse text-sm">
      <caption className="mb-2 text-left font-medium text-[var(--color-text)]">
        {caption}
      </caption>
      <thead>
        <tr>
          {selection ? (
            <th
              scope="col"
              className="border-b border-[var(--color-border)] p-2 text-left"
            >
              <Checkbox
                label={selection.selectAllLabel}
                labelHidden
                checked={allSelected}
                indeterminate={someSelected}
                onChange={(event) => selection.onSelectAll(event.target.checked)}
              />
            </th>
          ) : null}
          {columns.map((column) => (
            <th
              key={column.key}
              scope="col"
              className={[
                "border-b border-[var(--color-border)] p-2 text-xs font-semibold tracking-wide text-[var(--color-text-muted)] uppercase",
                column.align === "right" ? "text-right" : "text-left",
              ].join(" ")}
            >
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={columnCount} className="p-2 text-[var(--color-text-muted)]">
              {emptyState}
            </td>
          </tr>
        ) : (
          rows.map((row) => {
            const rowKey = getRowKey(row);
            return (
              <tr key={rowKey} className="hover:bg-[var(--color-surface-sunken)]">
                {selection ? (
                  <td className="border-b border-[var(--color-border)] p-2">
                    <Checkbox
                      label={selection.getRowLabel(row)}
                      labelHidden
                      checked={selection.selectedKeys.has(rowKey)}
                      onChange={(event) =>
                        selection.onSelectRow(rowKey, event.target.checked)
                      }
                    />
                  </td>
                ) : null}
                {columns.map((column) =>
                  column.key === rowHeaderKey ? (
                    <th
                      key={column.key}
                      scope="row"
                      className={[
                        "border-b border-[var(--color-border)] p-2 font-normal",
                        column.align === "right" ? "text-right" : "text-left",
                      ].join(" ")}
                    >
                      {column.render(row)}
                    </th>
                  ) : (
                    <td
                      key={column.key}
                      className={[
                        "border-b border-[var(--color-border)] p-2",
                        column.align === "right" ? "text-right" : "text-left",
                      ].join(" ")}
                    >
                      {column.render(row)}
                    </td>
                  ),
                )}
              </tr>
            );
          })
        )}
      </tbody>
    </table>
  );
}
