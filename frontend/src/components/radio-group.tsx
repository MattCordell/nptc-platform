import { useId, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import type { ChoiceOption } from "./choice-option.ts";

type RadioGroupProps = {
  /** The group's name, rendered as a `<legend>` - see `CheckboxGroup` for
   * why adjacent text is not an acceptable substitute (issue #210). */
  legend: string;
  options: ChoiceOption[];
  value: string | undefined;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: ReactNode;
  /** A caller-supplied id, landing on the *first option's input* - same
   * convention and same reasoning as `CheckboxGroup`. */
  id?: string;
  /** Submitted name shared by every radio in the group. Generated when
   * left off, so two groups on one screen never collide - two radio groups
   * sharing a name would behave as one group. */
  name?: string;
};

/**
 * A radio group labelled by a `<fieldset>`/`<legend>`, with the roving
 * tabindex and arrow-key traversal implemented here rather than left to
 * the browser (issue #210).
 *
 * That is the same call `Dialog` made about `showModal()` and for the same
 * reason: jsdom implements neither the roving tabindex nor arrow traversal
 * for native radios, so "one `Tab` stop, arrows move between options"
 * would be an untestable claim in CI if it were left implicit. Handling it
 * explicitly - and calling `preventDefault()` so a real browser's own
 * identical behaviour cannot also fire - keeps the contract the same
 * everywhere and asserted on every run.
 *
 * Selection follows focus, which is the platform behaviour for radios
 * (a group always has an answer once it has been entered), not an
 * invention of this component.
 */
export function RadioGroup({
  legend,
  options,
  value,
  onChange,
  hint,
  error,
  id: providedId,
  name,
}: RadioGroupProps) {
  const generatedId = useId();
  const generatedName = useId();
  const baseId = providedId ?? generatedId;
  const groupName = name ?? generatedName;
  const hintId = hint ? `${baseId}-hint` : undefined;
  const errorId = error ? `${baseId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  const enabledIndexes = options.reduce<number[]>((indexes, option, index) => {
    if (!option.disabled) {
      indexes.push(index);
    }
    return indexes;
  }, []);

  // The one tabbable stop: the selected option, or - before anything is
  // selected - the first enabled one, so the group is always reachable.
  // Every other option is tabIndex={-1}, which is what turns a five-radio
  // group from five tab stops into one.
  const selectedIndex = options.findIndex(
    (option) => option.value === value && !option.disabled,
  );
  const tabbableIndex = selectedIndex >= 0 ? selectedIndex : (enabledIndexes[0] ?? -1);

  const selectAt = (index: number) => {
    const option = options[index];
    if (!option) {
      return;
    }
    onChange(option.value);
    inputRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>, index: number) => {
    if (enabledIndexes.length === 0) {
      return;
    }
    // `index` is always an enabled option: a disabled input receives no
    // keyboard events, so there is no need to search for the nearest
    // enabled neighbour of a position that cannot be focused.
    const position = enabledIndexes.indexOf(index);

    const step = (delta: number) => {
      const wrapped = (position + delta + enabledIndexes.length) % enabledIndexes.length;
      selectAt(enabledIndexes[wrapped]);
    };

    switch (event.key) {
      case "ArrowDown":
      case "ArrowRight":
        event.preventDefault();
        step(1);
        break;
      case "ArrowUp":
      case "ArrowLeft":
        event.preventDefault();
        step(-1);
        break;
      case "Home":
        event.preventDefault();
        selectAt(enabledIndexes[0]);
        break;
      case "End":
        event.preventDefault();
        selectAt(enabledIndexes[enabledIndexes.length - 1]);
        break;
      default:
        break;
    }
  };

  return (
    <fieldset
      className="flex flex-col gap-2 border-0 p-0"
      aria-describedby={describedBy}
      aria-invalid={error ? true : undefined}
    >
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
              ref={(element) => {
                inputRefs.current[index] = element;
              }}
              type="radio"
              id={optionId}
              name={groupName}
              value={option.value}
              checked={option.value === value}
              disabled={option.disabled}
              tabIndex={index === tabbableIndex ? 0 : -1}
              onChange={() => onChange(option.value)}
              onKeyDown={(event) => handleKeyDown(event, index)}
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
