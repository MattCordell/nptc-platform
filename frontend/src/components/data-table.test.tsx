import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
});
