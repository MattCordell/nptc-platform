import type { ReactNode } from "react";

type Column<Row> = {
  key: string;
  header: string;
  /** Marks this column as the row header (`<th scope="row">`) rather than
   * a data cell - typically the row's identifying field. At most one
   * column should set this. */
  isRowHeader?: boolean;
  render: (row: Row) => ReactNode;
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
}: DataTableProps<Row>) {
  return (
    <table className="w-full border-collapse text-sm">
      <caption className="mb-2 text-left font-medium text-[var(--color-text)]">
        {caption}
      </caption>
      <thead>
        <tr>
          {columns.map((column) => (
            <th
              key={column.key}
              scope="col"
              className="border-b border-[var(--color-border)] p-2 text-left"
            >
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={columns.length} className="p-2 text-[var(--color-text-muted)]">
              {emptyState}
            </td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={getRowKey(row)}>
              {columns.map((column) =>
                column.isRowHeader ? (
                  <th
                    key={column.key}
                    scope="row"
                    className="border-b border-[var(--color-border)] p-2 text-left font-normal"
                  >
                    {column.render(row)}
                  </th>
                ) : (
                  <td
                    key={column.key}
                    className="border-b border-[var(--color-border)] p-2"
                  >
                    {column.render(row)}
                  </td>
                ),
              )}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
