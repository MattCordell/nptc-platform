import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { MultiSelectCombobox } from "./multi-select-combobox.tsx";
import type { MultiSelectOption } from "./multi-select-combobox.tsx";

const OPTIONS: MultiSelectOption[] = [
  { value: "chem", label: "Chemical pathology", count: 12 },
  { value: "haem", label: "Haematology", count: 7 },
  { value: "micro", label: "Microbiology", count: 3 },
];

/** Owns the selection as the page does, so a pick changes what the combobox shows. */
function Harness({
  options = OPTIONS,
  initial = [],
  maxSelected,
  onToggle,
}: {
  options?: MultiSelectOption[];
  initial?: string[];
  maxSelected?: number;
  onToggle?: (value: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>(initial);
  return (
    <MultiSelectCombobox
      label="Discipline"
      options={options}
      selected={selected}
      maxSelected={maxSelected}
      onToggle={(value) => {
        onToggle?.(value);
        setSelected((current) =>
          current.includes(value)
            ? current.filter((item) => item !== value)
            : [...current, value],
        );
      }}
    />
  );
}

describe("MultiSelectCombobox", () => {
  it("names the combobox by its label", () => {
    render(<Harness />);

    expect(screen.getByRole("combobox", { name: "Discipline" })).toBeInTheDocument();
  });

  it("lists every option with its count, in the order given, when opened", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));

    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Chemical pathology (12)",
      "Haematology (7)",
      "Microbiology (3)",
    ]);
  });

  it("narrows the list as the user types, ignoring case", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByRole("combobox", { name: "Discipline" }), "MICRO");

    const options = await screen.findAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Microbiology (3)");
  });

  it("says so when nothing matches what was typed", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByRole("combobox", { name: "Discipline" }), "zzz");

    expect(
      await screen.findByText("No discipline matches what you typed."),
    ).toBeVisible();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("does not blame the typed text when there are no options at all", async () => {
    const user = userEvent.setup();
    render(<Harness options={[]} initial={["chem"]} />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));

    expect(await screen.findByText("No other values match this search.")).toBeVisible();
    expect(screen.queryByText(/matches what you typed/)).not.toBeInTheDocument();
  });

  it("stays open after a pick, so several values come from one query", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Harness onToggle={onToggle} />);

    const input = screen.getByRole("combobox", { name: "Discipline" });
    await user.click(input);
    await user.click(await screen.findByRole("option", { name: /Chemical pathology/ }));
    await user.click(await screen.findByRole("option", { name: /Haematology/ }));

    expect(onToggle.mock.calls).toEqual([["chem"], ["haem"]]);
    expect(input).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps the input named while the popup is open", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));
    await screen.findAllByRole("option");

    expect(screen.getByRole("combobox", { name: "Discipline" })).toBeInTheDocument();
  });

  it("marks a selected option with aria-selected and a check mark", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["haem"]} />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));

    const selected = await screen.findByRole("option", { name: /Haematology/ });
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(within(selected).getByText("✓")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Microbiology/ })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("toggles a selected option off when it is picked again", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Harness initial={["haem"]} onToggle={onToggle} />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));
    await user.click(await screen.findByRole("option", { name: /Haematology/ }));

    expect(onToggle).toHaveBeenCalledWith("haem");
  });

  it("takes a pick from the keyboard alone: type, arrow, Enter", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Harness onToggle={onToggle} />);

    await user.tab();
    expect(screen.getByRole("combobox", { name: "Discipline" })).toHaveFocus();
    await user.keyboard("micro");
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");

    expect(onToggle).toHaveBeenCalledWith("micro");
  });

  it("closes on Escape and keeps focus in the input", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "Discipline" });

    await user.click(input);
    expect(await screen.findAllByRole("option")).not.toHaveLength(0);
    await user.keyboard("{Escape}");

    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveFocus();
  });

  it("reports how many values are selected where the input is empty", () => {
    const { rerender } = render(<Harness initial={[]} />);
    expect(screen.getByRole("combobox", { name: "Discipline" })).toHaveAttribute(
      "placeholder",
      "Any",
    );

    rerender(<Harness initial={["chem", "haem"]} key="two" />);
    expect(screen.getByRole("combobox", { name: "Discipline" })).toHaveAttribute(
      "placeholder",
      "2 selected",
    );
  });

  it("keeps a selected value the server no longer offers", () => {
    render(<Harness initial={["retired_code"]} />);

    expect(screen.getByRole("combobox", { name: "Discipline" })).toHaveAttribute(
      "placeholder",
      "1 selected",
    );
  });

  it("refuses a pick past the limit and announces why, and still lets one be removed", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Harness initial={["chem", "haem"]} maxSelected={2} onToggle={onToggle} />);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));
    await user.click(await screen.findByRole("option", { name: /Microbiology/ }));

    expect(onToggle).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        "You can choose at most 2 Discipline values. Remove one to choose another.",
      ),
    ).toBeInTheDocument();

    await user.click(await screen.findByRole("option", { name: /Haematology/ }));
    expect(onToggle).toHaveBeenCalledWith("haem");
  });

  it("copes with several hundred options and narrows them by typing", async () => {
    const many: MultiSelectOption[] = Array.from({ length: 600 }, (_, index) => ({
      value: `code-${index}`,
      label: `Specimen type ${index}`,
      count: 600 - index,
    }));
    const user = userEvent.setup();
    render(<Harness options={many} />);

    const input = screen.getByRole("combobox", { name: "Discipline" });
    await user.click(input);
    expect(await screen.findAllByRole("option")).toHaveLength(600);

    await user.type(input, "type 59");
    // "59", "590".."599"
    expect(await screen.findAllByRole("option")).toHaveLength(11);
  });

  it("has no axe violations, closed or open", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness initial={["chem"]} />);
    await expectNoA11yViolations(container);

    await user.click(screen.getByRole("combobox", { name: "Discipline" }));
    await expectNoA11yViolations(await screen.findByRole("listbox"));
  });
});
