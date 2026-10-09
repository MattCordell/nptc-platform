import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { FilterBar } from "./filter-bar.tsx";
import type { ActiveFilter } from "./filter-bar.tsx";

const CHIPS: ActiveFilter[] = [
  { key: "status:active", facetLabel: "Status", valueLabel: "Active" },
  { key: "discipline:chem", facetLabel: "Discipline", valueLabel: "Chemistry" },
];

describe("FilterBar active filters", () => {
  it("renders one chip per active filter in a named group", () => {
    render(<FilterBar activeFilters={CHIPS} />);

    const group = screen.getByRole("group", { name: "Active filters" });
    expect(
      within(group).getByRole("button", { name: "Remove filter Status: Active" }),
    ).toBeInTheDocument();
    expect(
      within(group).getByRole("button", { name: "Remove filter Discipline: Chemistry" }),
    ).toBeInTheDocument();
  });

  it("calls onRemove with the chip's key", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<FilterBar activeFilters={CHIPS} onRemove={onRemove} />);

    await user.click(
      screen.getByRole("button", { name: "Remove filter Discipline: Chemistry" }),
    );

    expect(onRemove).toHaveBeenCalledExactlyOnceWith("discipline:chem");
  });

  it("calls onClearAll from Clear all filters", async () => {
    const user = userEvent.setup();
    const onClearAll = vi.fn();
    render(<FilterBar activeFilters={CHIPS} onClearAll={onClearAll} />);

    await user.click(screen.getByRole("button", { name: "Clear all filters" }));

    expect(onClearAll).toHaveBeenCalledOnce();
  });

  it("removes a chip and clears all from the keyboard", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    const onClearAll = vi.fn();
    render(
      <FilterBar activeFilters={CHIPS} onRemove={onRemove} onClearAll={onClearAll} />,
    );

    await user.tab();
    await user.keyboard("{Enter}");
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "Clear all filters" })).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(onRemove).toHaveBeenCalledExactlyOnceWith("status:active");
    expect(onClearAll).toHaveBeenCalledOnce();
  });

  it("renders no chip row and no Clear all when nothing is active", () => {
    render(<FilterBar />);

    expect(screen.queryByRole("group", { name: "Active filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear all filters" })).toBeNull();
  });
});

describe("FilterBar", () => {
  it("renders an empty container for no props, rather than failing", () => {
    const { container } = render(<FilterBar />);

    expect(container.firstElementChild?.children).toHaveLength(0);
  });

  it("passes native props and className through to the root", () => {
    render(<FilterBar className="extra-class" data-testid="bar" />);

    expect(screen.getByTestId("bar").className).toContain("extra-class");
  });

  it("uses design tokens only: no hex values, palette classes or shadows", () => {
    const { container } = render(<FilterBar activeFilters={CHIPS} />);

    for (const element of container.querySelectorAll("*")) {
      expectTokenClassesOnly(element.getAttribute("class") ?? "");
    }
  });

  it("gives a chip a 20px radius token and a target above 24px", () => {
    render(<FilterBar activeFilters={CHIPS} />);

    const chip = screen.getByRole("button", { name: "Remove filter Status: Active" });
    expect(chip.className).toContain("rounded-[var(--radius-pill)]");
    expect(chip.className).toContain("min-h-8");
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <FilterBar
        activeFilters={CHIPS}
        onRemove={() => undefined}
        onClearAll={() => undefined}
      />,
    );

    await expectNoA11yViolations(container);
  });
});
