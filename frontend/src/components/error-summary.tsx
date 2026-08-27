import { useId } from "react";
import type { ReactNode, Ref } from "react";

/**
 * One field-level failure, as an error summary needs it: the message, and
 * the id of the control the message is about. The id is what makes the
 * summary actionable rather than merely informative - see `field.tsx`'s
 * `id` prop for how a caller gets one.
 */
export type FormError = {
  fieldId: string;
  message: string;
};

type ErrorSummaryProps = {
  errors: FormError[];
  /** A failure that belongs to no single field - typically a rejected save.
   * Rendered above the list, without a link, because there is nowhere
   * useful to send focus. */
  formError?: ReactNode;
  title?: string;
  /** React 19 takes `ref` as an ordinary prop, so this needs no
   * `forwardRef` - and none of the baseline components use one. `Form`
   * holds the ref to move focus here on a failed submit. */
  ref?: Ref<HTMLDivElement>;
};

/**
 * A summary of everything that failed validation, listed once at the top
 * of a form (issue #210).
 *
 * Focusable (`tabIndex={-1}`) but not `role="alert"`: a form moves focus
 * here on a failed submit, which announces the whole region - heading,
 * count and all - exactly once. An alert role on top of that announces it
 * a second time, the same double-announcement `field.tsx` records for its
 * inline error text.
 *
 * Each item is a real link, so it is reachable in a screen reader's link
 * list, but its default navigation is suppressed - see the click handler.
 */
export function ErrorSummary({ errors, formError, title, ref }: ErrorSummaryProps) {
  const titleId = useId();

  if (errors.length === 0 && !formError) {
    return null;
  }

  return (
    <div
      ref={ref}
      tabIndex={-1}
      aria-labelledby={titleId}
      className="flex flex-col gap-2 rounded-md border border-[var(--color-danger)] bg-[var(--color-danger-surface)] p-4"
    >
      <h2 id={titleId} className="text-base font-semibold text-[var(--color-danger)]">
        {title ?? "There is a problem"}
      </h2>
      {formError ? <p className="text-sm text-[var(--color-text)]">{formError}</p> : null}
      {errors.length > 0 ? (
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
          {errors.map((error, index) => (
            // Keyed by position as well as field: a single control can fail
            // two ways at once ("too long", "invalid characters"), and
            // keying on the field id alone would collide.
            <li key={`${error.fieldId}-${index}`}>
              <a
                href={`#${error.fieldId}`}
                className="text-[var(--color-danger)] underline"
                onClick={(event) => {
                  // preventDefault, then focus explicitly: the fragment is
                  // kept in the href so this reads and behaves as a link,
                  // but following it would push a hash the TanStack router
                  // treats as navigation, and a fragment lands focus on the
                  // target inconsistently across browsers (and not at all
                  // in jsdom). Moving focus by hand is what makes "and
                  // where" true rather than aspirational.
                  event.preventDefault();
                  document.getElementById(error.fieldId)?.focus();
                }}
              >
                {error.message}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
