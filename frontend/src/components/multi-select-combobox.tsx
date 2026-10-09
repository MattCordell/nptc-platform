import { Combobox } from "@base-ui/react/combobox";
import { useState } from "react";
import type { ReactNode } from "react";

import { Field } from "./field.tsx";
import { INPUT_CLASSES } from "./input-classes.ts";

export type MultiSelectOption = {
  value: string;
  label: string;
  /** Result count for this option (FR-16). Omit when counts are unknown. */
  count?: number;
};

type MultiSelectComboboxProps = {
  label: string;
  /** Passed through to `Field`, for something outside the control that has to
   * address it by id. */
  id?: string;
  hint?: ReactNode;
  /** Every option on offer, in the order to show them. Typing narrows this list
   * in the browser; nothing here is fetched as the user types. */
  options: readonly MultiSelectOption[];
  /** The selected values, which need not all be in `options`: a value a link
   * carries that the server no longer offers is still selected, and a user
   * must be able to deselect it. */
  selected: readonly string[];
  /** Called once per value the user adds or removes. */
  onToggle: (value: string) => void;
  /** The most values one selection may hold. At the limit, adding another is
   * refused with a message in the popup and removing still works. */
  maxSelected?: number;
  disabled?: boolean;
};

const ITEM_CLASSES =
  "flex min-h-8 cursor-pointer select-none items-center gap-2 px-2 py-1 text-sm text-[var(--color-text)] data-[highlighted]:bg-[var(--color-accent)] data-[highlighted]:text-[var(--color-accent-contrast)]";

const TRIGGER_CLASSES =
  "absolute inset-y-0 right-0 flex w-8 cursor-pointer items-center justify-center rounded-r-[var(--radius-control)] border-0 bg-transparent p-0 text-[var(--color-text)]";

const itemLabel = (option: MultiSelectOption): string => option.label;
const sameOption = (a: MultiSelectOption, b: MultiSelectOption): boolean =>
  a.value === b.value;

/**
 * A multi-select combobox that lists every option at once and narrows it as
 * the user types (FR-16, WAI-ARIA combobox pattern). It stays open after a
 * pick, so several values come from one typed query, and each selected option
 * carries a check mark as well as its fill, so selection never rests on colour
 * alone (NFR-31).
 *
 * Holds no selection of its own: the caller owns `selected`, usually in the
 * URL, and is told each change through `onToggle`. It draws no chips either,
 * since the caller shows the selection beside every other active filter.
 */
export function MultiSelectCombobox({
  label,
  id,
  hint,
  options,
  selected,
  onToggle,
  maxSelected,
  disabled,
}: MultiSelectComboboxProps) {
  const [limitReached, setLimitReached] = useState(false);

  const byValue = new Map(options.map((option) => [option.value, option]));
  const value = selected.map(
    (selectedValue) =>
      byValue.get(selectedValue) ?? { value: selectedValue, label: selectedValue },
  );

  function handleValueChange(next: MultiSelectOption[]) {
    const nextValues = new Set(next.map((option) => option.value));
    const removed = selected.filter((current) => !nextValues.has(current));
    const added = next.filter((option) => !selected.includes(option.value));

    if (removed.length > 0) {
      setLimitReached(false);
    }
    removed.forEach(onToggle);
    for (const option of added) {
      if (maxSelected !== undefined && selected.length - removed.length >= maxSelected) {
        setLimitReached(true);
        return;
      }
      onToggle(option.value);
    }
  }

  return (
    <Field label={label} id={id} hint={hint} className="min-w-56 flex-1">
      {(controlProps) => (
        <Combobox.Root<MultiSelectOption, true>
          multiple
          items={options}
          value={value}
          onValueChange={handleValueChange}
          itemToStringLabel={itemLabel}
          isItemEqualToValue={sameOption}
          disabled={disabled}
          // The popup stays open after a pick, and the typed query stays, so a
          // user can tick several matches without typing again.
          onOpenChange={(open, details) => {
            if (!open && details.reason === "item-press") {
              details.cancel();
              return;
            }
            setLimitReached(false);
          }}
          onInputValueChange={(_text, details) => {
            if (details.isItemPress) {
              details.cancel();
            }
          }}
        >
          <Combobox.InputGroup className="relative">
            <Combobox.Input
              {...controlProps}
              // Base UI hides the visible label from assistive technology while
              // the popup is open, which would leave the input unnamed.
              aria-label={label}
              placeholder={selected.length > 0 ? `${selected.length} selected` : "Any"}
              className={`${INPUT_CLASSES} pr-9`}
            />
            <Combobox.Trigger
              aria-label={`Show ${label} options`}
              className={TRIGGER_CLASSES}
            >
              <span aria-hidden="true">▾</span>
            </Combobox.Trigger>
          </Combobox.InputGroup>
          <Combobox.Portal>
            <Combobox.Positioner sideOffset={4} className="z-50 outline-none">
              <Combobox.Popup className="max-h-72 w-[var(--anchor-width)] max-w-[var(--available-width)] overflow-y-auto rounded-[var(--radius-control)] border border-[var(--color-border)] bg-[var(--color-surface)] py-1">
                {/* Inside the popup, because Base UI marks everything outside it
                    inert while it is open, and an inert live region is not read. */}
                <Combobox.Status className="px-2 text-sm text-[var(--color-danger)]">
                  {limitReached
                    ? `You can choose at most ${maxSelected} ${label} values. Remove one to choose another.`
                    : null}
                </Combobox.Status>
                <Combobox.Empty className="px-2 py-2 text-sm text-[var(--color-text-muted)]">
                  No {label.toLowerCase()} matches what you typed.
                </Combobox.Empty>
                <Combobox.List>
                  {(option: MultiSelectOption) => (
                    <Combobox.Item
                      key={option.value}
                      value={option}
                      className={ITEM_CLASSES}
                    >
                      <span className="w-4 shrink-0" aria-hidden="true">
                        <Combobox.ItemIndicator>✓</Combobox.ItemIndicator>
                      </span>
                      <span>
                        {option.count === undefined
                          ? option.label
                          : `${option.label} (${option.count})`}
                      </span>
                    </Combobox.Item>
                  )}
                </Combobox.List>
              </Combobox.Popup>
            </Combobox.Positioner>
          </Combobox.Portal>
        </Combobox.Root>
      )}
    </Field>
  );
}
