import { useId } from "react";
import type { ReactNode } from "react";

import type { ChoiceOption } from "./choice-option.ts";

type CheckboxGroupProps = {
  /** The group's name, rendered as a `<legend>`. Required: the issue is
   * explicit that a group labelled by adjacent text alone is not labelled
   * (issue #210) - a screen reader announces a `<legend>` when focus
   * enters the group, and ignores a nearby paragraph. */
  legend: string;
  options: ChoiceOption[];
  value: string[];
  onChange: (value: string[]) => void;
  hint?: ReactNode;
  error?: ReactNode;
  /** A caller-supplied id. It lands on the *first option's input*, not on
   * the `<fieldset>`, so an `ErrorSummary` item linking to `#id` moves
   * focus to a real control: focusing a fieldset announces nothing useful,
   * whereas focusing the first checkbox announces the legend as the group
   * it belongs to, then the option itself. Remaining options derive
   * `${id}-1`, `${id}-2`, ... */
  id?: string;
  /** Submitted name shared by every box in the group. Generated when left
   * off, so two groups on one screen never collide. */
  name?: string;
};

/**
 * A group of checkboxes labelled by a `<fieldset>`/`<legend>` (issue
 * #210). Each box keeps its own tab stop - unlike `RadioGroup`, which
 * rovers - because a checkbox group is a set of independent yes/no
 * answers, and skipping past one with a single `Tab` would hide it.
 */
export function CheckboxGroup({
  legend,
  options,
  value,
  onChange,
  hint,
  error,
  id: providedId,
  name,
}: CheckboxGroupProps) {
  const generatedId = useId();
  const generatedName = useId();
  const baseId = providedId ?? generatedId;
  const groupName = name ?? generatedName;
  const hintId = hint ? `${baseId}-hint` : undefined;
  const errorId = error ? `${baseId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  const toggle = (optionValue: string) => {
    const selected = new Set(value);
    if (selected.has(optionValue)) {
      selected.delete(optionValue);
    } else {
      selected.add(optionValue);
    }
    // Known options are reported in the order they were declared, not in
    // click order: a caller comparing the new value to the old one (a dirty
    // check, an equality assertion) should not see a difference that is only
    // about which box the user happened to tick first.
    //
    // Values with no matching option are carried through untouched rather
    // than dropped. A stored entry can legitimately hold a code that is no
    // longer offered - a retired specimen type, a value from an older
    // version of the list - and re-deriving the whole array from `options`
    // would delete it the moment the user ticked something unrelated. That
    // is silent data loss on an interaction that had nothing to do with it.
    const known = new Set(options.map((option) => option.value));
    onChange([
      ...options.filter((option) => selected.has(option.value)).map((o) => o.value),
      ...value.filter((held) => !known.has(held)),
    ]);
  };

  // `aria-describedby`/`aria-invalid` go on each `<input>`, not on the
  // `<fieldset>`. A group role's description is inconsistently announced -
  // NVDA and JAWS commonly skip `aria-describedby` on a `group`, and
  // `aria-invalid` is not reliably supported there at all - so the hint and
  // error would be visible but silent, while the same text on a `Field`
  // control is announced. Per-control support is universal, and putting it
  // in both places instead would announce the hint twice on the first
  // option.
  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0">
      <legend className="text-sm font-medium text-[var(--color-text)]">{legend}</legend>
      {hint ? (
        <p id={hintId} className="text-sm text-[var(--color-text-muted)]">
          {hint}
        </p>
      ) : null}
      {options.map((option, index) => {
        const optionId = index === 0 ? baseId : `${baseId}-${index}`;
        return (
          <div key={option.value} className="flex items-center gap-2">
            <input
              type="checkbox"
              id={optionId}
              name={groupName}
              value={option.value}
              checked={value.includes(option.value)}
              disabled={option.disabled}
              aria-describedby={describedBy}
              aria-invalid={error ? true : undefined}
              onChange={() => toggle(option.value)}
              className="h-4 w-4"
            />
            <label htmlFor={optionId} className="text-sm text-[var(--color-text)]">
              {option.label}
            </label>
          </div>
        );
      })}
      {error ? (
        <p id={errorId} className="text-sm text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
