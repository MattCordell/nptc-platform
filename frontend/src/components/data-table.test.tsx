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

  // The next two assert the Tailwind class contract, not rendered style -
  // jsdom has no layout engine, so there is no computed colour or hover
  // state to check (PR #327 review). They are change-detectors for the
  // class string in data-table.tsx, not visual proof of the styling; see
  // frontend/tests/design-tokens-contrast.test.ts for the one place token
  // *values* are checked.
  it("styles the header row uppercase, small and muted, per the dense-table pattern", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    const header = screen.getByRole("columnheader", { name: "Code" });
    expect(header.className).toContain("uppercase");
    expect(header.className).toContain("tracking-wide");
    expect(header.className).toContain("text-[var(--color-text-muted)]");
  });

  it("fills the surface-sunken colour on row hover", () => {
    render(
      <DataTable
        caption="Catalogue entries"
        columns={COLUMNS}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    const row = screen.getByRole("rowheader", { name: "NPTC-1" }).closest("tr");
    expect(row).not.toBeNull();
    expect(row!.className).toContain("hover:bg-[var(--color-surface-sunken)]");
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

  it("right-aligns a column's header and data cells when align is right", () => {
    const columnsWithAlignedStatus = [
      { key: "id", header: "Code", isRowHeader: true, render: (row: Entry) => row.id },
      {
        key: "status",
        header: "Status",
        align: "right" as const,
        render: (row: Entry) => row.status,
      },
    ];

    render(
      <DataTable
        caption="Catalogue entries"
        columns={columnsWithAlignedStatus}
        rows={ENTRIES}
        getRowKey={(row) => row.id}
        emptyState="No entries"
      />,
    );

    expect(screen.getByRole("columnheader", { name: "Status" }).className).toContain(
      "text-right",
    );
    expect(screen.getByRole("cell", { name: "Active" }).className).toContain(
      "text-right",
    );
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
