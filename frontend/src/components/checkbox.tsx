import { useId } from "react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

type CheckboxProps = {
  /** The visible label text. Required, for the same reason `Field`'s is
   * (issue #148): there is no unlabelled fallback. */
  label: string;
  /** A caller-supplied id for the input - an `ErrorSummary` item links to
   * `#id` to move focus here (issue #210). Generated with `useId` when
   * left off. */
  id?: string;
  hint?: ReactNode;
  /** Validation message, linked via `aria-describedby`. Not `role="alert"`,
   * for the reason recorded in `field.tsx`: a control that already
   * describes itself by this text would otherwise announce it twice. */
  error?: ReactNode;
} & Omit<ComponentPropsWithoutRef<"input">, "id" | "type" | "children">;

/**
 * A single labelled checkbox (issue #210).
 *
 * Deliberately does not compose `Field`, unlike `Select`: a checkbox's
 * label belongs *after* the box, and `Field` renders the label first by
 * construction. Reusing it would mean either a layout escape hatch on
 * `Field` or a checkbox whose label reads in the wrong place, so the
 * label/hint/error wiring is repeated here - matching `Field`'s id scheme
 * (`${id}-hint`, `${id}-error`) and describedby ordering exactly, so the
 * two behave the same even though they are two implementations.
 */
export function Checkbox({
  id: providedId,
  label,
  hint,
  error,
  className,
  ...rest
}: CheckboxProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          {...rest}
          type="checkbox"
          id={id}
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          className={["h-4 w-4", className ?? ""].filter(Boolean).join(" ")}
        />
        <label htmlFor={id} className="text-sm font-medium text-[var(--color-text)]">
          {label}
        </label>
      </div>
      {hint ? (
        <p id={hintId} className="text-sm text-[var(--color-text-muted)]">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
