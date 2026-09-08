import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { DataTable } from "./data-table.tsx";

type Entry = { id: string; term: string; status: string };

const ENTRIES: Entry[] = [
  { id: "NPTC-1", term: "Full blood count", status: "Active" },
  { id: "NPTC-2", term: "Urea and electrolytes", status: "Retired" },
];

const COLUMNS = [
  { key: "id", header: "Code", isRowHeader: true, render: (row: Entry) => row.id },
  { key: "term", header: "Requesting term", render: (row: Entry) => row.term },
  { key: "status", header: "Status", render: (row: Entry) => row.status },
];

describe("DataTable", () => {
  it("renders a caption naming the table", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    expect(screen.getByRole("table", { name: "Catalogue entries" })).toBeInTheDocument();
  });

  it("gives each column header scope=col", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    for (const column of COLUMNS) {
      expect(screen.getByRole("columnheader", { name: column.header })).toHaveAttribute(
        "scope",
        "col",
      );
    }
  });

  it("gives the designated column scope=row on each data row", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    const rowHeader = screen.getByRole("rowheader", { name: "NPTC-1" });
    expect(rowHeader).toHaveAttribute("scope", "row");
    // and it is inside the row it identifies, alongside that row's data
    const row = rowHeader.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row!).getByText("Full blood count")).toBeInTheDocument();
  });

  it("treats only the first isRowHeader column as the row header when a caller declares two", () => {
    const columnsWithTwoRowHeaders = [
      { key: "id", header: "Code", isRowHeader: true, render: (row: Entry) => row.id },
      {
        key: "term",
        header: "Requesting term",
        isRowHeader: true,
        render: (row: Entry) => row.term,
      },
      { key: "status", header: "Status", render: (row: Entry) => row.status },
    ];

    render(
      <DataTable
        caption="Catalogue entries"
        columns={columnsWithTwoRowHeaders}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    // Exactly one <th scope="row"> per row - "term" degrades to a plain
    // data cell rather than also becoming a row header.
    expect(screen.getAllByRole("rowheader")).toHaveLength(ENTRIES.length);
    expect(screen.getByRole("rowheader", { name: "NPTC-1" })).toBeInTheDocument();
    expect(
      screen.queryByRole("rowheader", { name: "Full blood count" }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state, not a headers-only table, when there are no rows", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={[]}
        getRowKey={(row) => row.id}
        emptyState="No entries match this filter"
      />,
    );

    expect(screen.getByText("No entries match this filter")).toBeInTheDocument();
    expect(screen.queryByRole("rowheader")).not.toBeInTheDocument();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    await expectNoA11yViolations(container);
  });

  describe("row selection (issue #267)", () => {
    /** Owns selection state itself, matching how a real caller (the
     * screen, not this table) would - the tests below exercise the actual
     * select/deselect/select-all round trip, not a mocked-out callback. */
    function SelectableTable({ initial = [] as string[] }: { initial?: string[] }) {
      const [selected, setSelected] = useState<Set<string>>(new Set(initial));
      return (
        <DataTable
          caption="Catalogue entries"
          columns={COLUMNS}
          rows={ENTRIES}
          getRowKey={(row) => row.id}
          emptyState="No entries"
          selection={{
            selectedKeys: selected,
            onSelectRow: (key, isSelected) =>
              setSelected((current) => {
                const next = new Set(current);
                if (isSelected) {
                  next.add(key);
                } else {
                  next.delete(key);
                }
                return next;
              }),
            onSelectAll: (isSelected) =>
              setSelected(isSelected ? new Set(ENTRIES.map((row) => row.id)) : new Set()),
            selectAllLabel: "Select all rows",
            getRowLabel: (row) => `Select ${row.id}`,
          }}
        />
      );
    }

    it("adds a leading selection column with an accessibly labelled box per row", () => {
      render(<SelectableTable />);

      expect(screen.getByRole("checkbox", { name: "Select NPTC-1" })).not.toBeChecked();
      expect(screen.getByRole("checkbox", { name: "Select NPTC-2" })).not.toBeChecked();
    });

    it("selects and deselects one row independently of the others", async () => {
      const user = userEvent.setup();
      render(<SelectableTable />);

      await user.click(screen.getByRole("checkbox", { name: "Select NPTC-1" }));

      expect(screen.getByRole("checkbox", { name: "Select NPTC-1" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "Select NPTC-2" })).not.toBeChecked();

      await user.click(screen.getByRole("checkbox", { name: "Select NPTC-1" }));

      expect(screen.getByRole("checkbox", { name: "Select NPTC-1" })).not.toBeChecked();
    });

    it("selects and deselects every row from the select-all box", async () => {
      const user = userEvent.setup();
      render(<SelectableTable />);

      await user.click(screen.getByRole("checkbox", { name: "Select all rows" }));

      for (const row of ENTRIES) {
        expect(screen.getByRole("checkbox", { name: `Select ${row.id}` })).toBeChecked();
      }

      await user.click(screen.getByRole("checkbox", { name: "Select all rows" }));

      for (const row of ENTRIES) {
        expect(screen.getByRole("checkbox", { name: `Select ${row.id}` })).not.toBeChecked();
      }
    });

    it("marks the select-all box checked once every row is individually selected", async () => {
      const user = userEvent.setup();
      render(<SelectableTable />);

      for (const row of ENTRIES) {
        await user.click(screen.getByRole("checkbox", { name: `Select ${row.id}` }));
      }

      expect(screen.getByRole("checkbox", { name: "Select all rows" })).toBeChecked();
    });

    it("marks the select-all box indeterminate, not checked, for a partial selection", async () => {
      const user = userEvent.setup();
      render(<SelectableTable />);

      await user.click(screen.getByRole("checkbox", { name: "Select NPTC-1" }));

      const selectAll = screen.getByRole("checkbox", { name: "Select all rows" });
      expect(selectAll).not.toBeChecked();
      expect((selectAll as HTMLInputElement).indeterminate).toBe(true);
    });

    it("is operable by keyboard alone", async () => {
      const user = userEvent.setup();
      render(<SelectableTable />);

      await user.tab();
      expect(screen.getByRole("checkbox", { name: "Select all rows" })).toHaveFocus();

      await user.tab();
      expect(screen.getByRole("checkbox", { name: "Select NPTC-1" })).toHaveFocus();

      await user.keyboard("{ }");
      expect(screen.getByRole("checkbox", { name: "Select NPTC-1" })).toBeChecked();
    });

    it("accounts for the extra column in the empty-state colspan", () => {
      const onSelectAll = vi.fn();
      const onSelectRow = vi.fn();
      render(
        <DataTable
          caption="Catalogue entries"
          columns={COLUMNS}
          rows={[]}
          getRowKey={(row) => row.id}
          emptyState="No entries match this filter"
          selection={{
            selectedKeys: new Set(),
            onSelectRow,
            onSelectAll,
            selectAllLabel: "Select all rows",
            getRowLabel: (row) => `Select ${row.id}`,
          }}
        />,
      );

      const emptyCell = screen.getByText("No entries match this filter");
      expect(emptyCell).toHaveAttribute("colspan", String(COLUMNS.length + 1));
    });

    it("has no automated accessibility violations with a selection column", async () => {
      const { container } = render(<SelectableTable initial={["NPTC-1"]} />);

      await expectNoA11yViolations(container);
    });
  });
});
