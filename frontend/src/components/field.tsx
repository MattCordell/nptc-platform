import { useId } from "react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

type FieldProps = {
  /** The visible label text. Required - a field with no label is the exact
   * defect this component exists to make impossible (issue #148). */
  label: string;
  /** A caller-supplied id for the *control* (not the wrapper element). Left
   * off, the id is generated with `useId` as before. Supply one when
   * something outside the field needs to address the control by id - an
   * `ErrorSummary` item links to `#id` to move focus to the field it names
   * (issue #210), which it cannot do for an id only this component knows. */
  id?: string;
  /** Optional supporting text, rendered below the label and linked via
   * `aria-describedby` so it is announced with the field, not just visible. */
  hint?: ReactNode;
  /** Validation message. When present, the field is rendered in its error
   * state: `aria-invalid="true"` on the control, and the message linked via
   * `aria-describedby` alongside any hint. Not `role="alert"` - a control
   * with a screen reader's focus already visits its `aria-describedby`
   * text, so an alert role on top of that announces it twice. A submit-time
   * validation summary should announce through `LiveRegion` instead. */
  error?: ReactNode;
  /** The control to render inside the field - typically an `<input>`,
   * `<textarea>`, or `<select>`. Receives `id`, `aria-describedby`, and
   * `aria-invalid` from the field; the caller supplies everything else
   * (`type`, `value`, `onChange`, ...). */
  children: (controlProps: {
    id: string;
    "aria-describedby": string | undefined;
    "aria-invalid": boolean | undefined;
  }) => ReactNode;
} & Omit<ComponentPropsWithoutRef<"div">, "children" | "id">;

/**
 * A form field with a programmatically associated label (issue #148's first
 * baseline component). The association is structural, generated via
 * `useId`, rather than left for a caller to remember to wire up by hand.
 *
 * Deliberately takes a render-prop for the control rather than cloning a
 * fixed set of child elements: the field only owns the label/hint/error
 * wiring, not the control's own props, so it composes with any input type
 * (text, select, textarea, ...) without this component needing to know
 * about all of them.
 */
export function Field({
  id: providedId,
  label,
  hint,
  error,
  children,
  className,
  ...rest
}: FieldProps) {
  // `id` is deliberately Omit-ed from the wrapper's props above rather than
  // left to land on the <div> via `...rest`: an id on the container would
  // silently do nothing for the summary link that needs it, and having the
  // same prop name mean two different elements is worse than picking one.
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`flex flex-col gap-1 ${className ?? ""}`.trim()} {...rest}>
      <label htmlFor={id} className="text-sm font-medium text-[var(--color-text)]">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-sm text-[var(--color-text-muted)]">
          {hint}
        </p>
      ) : null}
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })}
      {error ? (
        <p id={errorId} className="text-sm text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
