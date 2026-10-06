import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { expectTokenClassesOnly } from "../test/token-classes.ts";
import { FilterBar } from "./filter-bar.tsx";
import type { ActiveFilter, FilterDropdown, FilterToggleGroup } from "./filter-bar.tsx";

function statusGroup(overrides: Partial<FilterToggleGroup> = {}): FilterToggleGroup {
  return {
    label: "Status",
    options: [
      { value: "active", label: "Active", count: 12 },
      { value: "draft", label: "Draft", count: 3 },
      { value: "deprecated", label: "Deprecated" },
    ],
    selected: [],
    onToggle: () => undefined,
    ...overrides,
  };
}

function disciplineDropdown(overrides: Partial<FilterDropdown> = {}): FilterDropdown {
  return {
    label: "Discipline",
    options: [
      { value: "chem", label: "Chemistry", count: 7 },
      { value: "haem", label: "Haematology" },
    ],
    value: "",
    onChange: () => undefined,
    placeholder: "Any discipline",
    ...overrides,
  };
}

const CHIPS: ActiveFilter[] = [
  { key: "status:active", facetLabel: "Status", valueLabel: "Active" },
  { key: "discipline:chem", facetLabel: "Discipline", valueLabel: "Chemistry" },
];

describe("FilterBar toggles", () => {
  it("renders each toggle group as a named group of buttons", () => {
    render(<FilterBar toggleGroups={[statusGroup()]} />);

    const group = screen.getByRole("group", { name: "Status" });
    expect(within(group).getAllByRole("button")).toHaveLength(3);
  });

  it("reports pressed state through aria-pressed, not only through colour", () => {
    render(<FilterBar toggleGroups={[statusGroup({ selected: ["draft"] })]} />);

    expect(screen.getByRole("button", { name: "Active (12)" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    const draft = screen.getByRole("button", { name: "Draft (3)" });
    expect(draft).toHaveAttribute("aria-pressed", "true");
    expect(draft).toHaveTextContent("✓");
    expect(draft.className).toContain("font-semibold");
    expect(screen.getByRole("button", { name: "Active (12)" })).not.toHaveTextContent(
      "✓",
    );
  });

  it("includes the count in the name when one is given, and omits it otherwise", () => {
    render(<FilterBar toggleGroups={[statusGroup()]} />);

    expect(screen.getByRole("button", { name: "Active (12)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deprecated" })).toBeInTheDocument();
  });

  it("shows a count of zero, since none match is still a count", () => {
    render(
      <FilterBar
        toggleGroups={[
          statusGroup({ options: [{ value: "draft", label: "Draft", count: 0 }] }),
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Draft (0)" })).toBeInTheDocument();
  });

  it("calls onToggle with the option's value", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<FilterBar toggleGroups={[statusGroup({ onToggle })]} />);

    await user.click(screen.getByRole("button", { name: "Draft (3)" }));

    expect(onToggle).toHaveBeenCalledExactlyOnceWith("draft");
  });

  it("toggles from the keyboard with Enter and Space", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<FilterBar toggleGroups={[statusGroup({ onToggle })]} />);

    await user.tab();
    expect(screen.getByRole("button", { name: "Active (12)" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.tab();
    await user.keyboard("{ }");

    expect(onToggle.mock.calls).toEqual([["active"], ["draft"]]);
  });
});

describe("FilterBar dropdowns", () => {
  it("renders a labelled select with counts in the option text", () => {
    render(<FilterBar dropdowns={[disciplineDropdown()]} />);

    const select = screen.getByLabelText("Discipline");
    expect(select.tagName).toBe("SELECT");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Any discipline", "Chemistry (7)", "Haematology"]);
  });

  it("reports the chosen value, and the empty string for no filter", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FilterBar dropdowns={[disciplineDropdown({ value: "chem", onChange })]} />);

    await user.selectOptions(screen.getByLabelText("Discipline"), "haem");
    await user.selectOptions(screen.getByLabelText("Discipline"), "");

    expect(onChange.mock.calls).toEqual([["haem"], [""]]);
  });

  it("is reachable by keyboard alone", async () => {
    const user = userEvent.setup();
    render(<FilterBar dropdowns={[disciplineDropdown()]} />);

    await user.tab();

    expect(screen.getByLabelText("Discipline")).toHaveFocus();
  });
});

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
    render(<FilterBar toggleGroups={[statusGroup()]} />);

    expect(screen.queryByRole("group", { name: "Active filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear all filters" })).toBeNull();
  });

  it("renders no controls row when only chips are given", () => {
    const { container } = render(<FilterBar activeFilters={CHIPS} />);

    const root = container.firstElementChild as HTMLElement;
    expect(root.children).toHaveLength(1);
    expect(root.firstElementChild).toBe(
      screen.getByRole("group", { name: "Active filters" }),
    );
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
    const { container } = render(
      <FilterBar
        toggleGroups={[statusGroup({ selected: ["active"] })]}
        dropdowns={[disciplineDropdown()]}
        activeFilters={CHIPS}
      />,
    );

    for (const element of container.querySelectorAll("*")) {
      expectTokenClassesOnly(element.getAttribute("class") ?? "");
    }
  });

  it("gives pills a 20px radius token and a target above 24px", () => {
    render(<FilterBar toggleGroups={[statusGroup()]} activeFilters={CHIPS} />);

    for (const pill of [
      screen.getByRole("button", { name: "Active (12)" }),
      screen.getByRole("button", { name: "Remove filter Status: Active" }),
    ]) {
      expect(pill.className).toContain("rounded-[var(--radius-pill)]");
      expect(pill.className).toContain("min-h-8");
    }
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <FilterBar
        toggleGroups={[statusGroup({ selected: ["active"] })]}
        dropdowns={[disciplineDropdown({ value: "chem" })]}
        activeFilters={CHIPS}
        onRemove={() => undefined}
        onClearAll={() => undefined}
      />,
    );

    await expectNoA11yViolations(container);
  });
});
