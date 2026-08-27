import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { Field } from "./field.tsx";

export type SelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

type SelectProps = {
  label: string;
  /** Passed through to `Field` - see `field.tsx` for why an id is worth
   * supplying (an `ErrorSummary` item links to `#id`). */
  id?: string;
  hint?: ReactNode;
  error?: ReactNode;
  options: SelectOption[];
  /** Rendered as an empty-valued first option. Without one, a native
   * `<select>` reports its first option as the value, so a field the user
   * never touched looks answered - "choose one" has to be a real state the
   * caller can validate against, not an accident of option ordering.
   *
   * Deliberately *not* `disabled`: the HTML selectedness algorithm skips
   * disabled options when picking the default, so a disabled placeholder is
   * silently passed over and the first real option is selected instead -
   * exactly the defect the placeholder exists to prevent. Leaving it
   * enabled also lets a user who chose by mistake get back to "none". */
  placeholder?: string;
} & Omit<ComponentPropsWithoutRef<"select">, "id" | "children">;

/**
 * A labelled select (issue #210). A native `<select>`, not a custom
 * listbox: the issue rules one out unless native genuinely cannot express
 * the need, and it cannot here - the browser's own control already gives
 * keyboard operation, type-ahead, and the platform's native mobile picker,
 * none of which a hand-built `role="listbox"` reproduces for free.
 *
 * Composes `Field` rather than re-deriving the label/hint/error wiring, so
 * there is exactly one implementation of "how a control is described"
 * (issue #148's rule) and a select cannot drift from a text input.
 */
export function Select({
  label,
  id,
  hint,
  error,
  options,
  placeholder,
  className,
  ...rest
}: SelectProps) {
  return (
    <Field label={label} id={id} hint={hint} error={error}>
      {(controlProps) => (
        <select
          // `rest` first, then the field's wiring: a caller passing
          // `aria-describedby` or `aria-invalid` by hand must not be able to
          // silently unpick the association `Field` just made.
          {...rest}
          {...controlProps}
          className={[
            "rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]",
            error ? "border-[var(--color-danger)]" : "",
            className ?? "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {placeholder ? <option value="">{placeholder}</option> : null}
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
