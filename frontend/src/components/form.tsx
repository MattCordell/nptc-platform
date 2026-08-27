import { useEffect, useRef } from "react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { Button } from "./button.tsx";
import { ErrorSummary } from "./error-summary.tsx";
import type { FormError } from "./error-summary.tsx";

type FormProps = {
  /** Called once per accepted submit, already `preventDefault`-ed. Takes no
   * event: a caller that needed the event would be reaching around the one
   * submit path this component exists to provide. */
  onSubmit: () => void;
  /** Field-level failures the caller has computed. Passing a non-empty list
   * after a submit attempt is what moves focus to the summary. */
  errors?: FormError[];
  /** A failure that belongs to no single field - in practice a rejected
   * save: the API's 422 shape is `ErrorResponse { detail: string }` with no
   * per-field `loc`, so a server refusal cannot be attributed to a control
   * and belongs here rather than on one. */
  formError?: ReactNode;
  pending?: boolean;
  /** Required - `Form` renders its own submit button, so "one submit path"
   * is structural rather than a convention a screen has to remember. */
  submitLabel: string;
  pendingLabel?: string;
  /** Cancel and friends, rendered beside the submit button. */
  secondaryActions?: ReactNode;
  errorSummaryTitle?: string;
  children: ReactNode;
} & Omit<ComponentPropsWithoutRef<"form">, "onSubmit" | "children">;

/**
 * A form wrapper owning the three things every edit screen otherwise
 * re-invents (issue #210): one submit path, a pending state, and telling a
 * screen-reader user what failed and where.
 *
 * It renders the submit button itself rather than accepting one as a
 * child. That is the opinionated part, and it is deliberate: "one submit
 * path" and "submitting is disabled while a save is in flight" are only
 * guarantees if nothing else can put a `type="submit"` control in the
 * form, and only testable if this component owns the control under test.
 * `secondaryActions` is the escape hatch for everything that is not the
 * submit.
 */
export function Form({
  onSubmit,
  errors,
  formError,
  pending = false,
  submitLabel,
  pendingLabel,
  secondaryActions,
  errorSummaryTitle,
  children,
  className,
  ...rest
}: FormProps) {
  const summaryRef = useRef<HTMLDivElement>(null);
  const awaitingResultRef = useRef(false);
  const fieldErrors = errors ?? [];
  const hasErrors = fieldErrors.length > 0 || Boolean(formError);

  // Focus is moved in an effect, not in the submit handler, because the
  // errors are the caller's to compute: they arrive as props on the render
  // that follows the submit, and for a server refusal they may not arrive
  // for several. The flag is what distinguishes "these errors are the
  // answer to a submit the user just made" - worth interrupting them for -
  // from errors that were on screen all along.
  //
  // `pending` is how a caller says "the answer has not arrived yet", so it
  // is also what holds the flag open across an async submit; once pending
  // is false the submit has been answered either way, and the flag is
  // cleared so a later, unrelated error cannot steal focus.
  useEffect(() => {
    if (!awaitingResultRef.current || pending) {
      return;
    }
    awaitingResultRef.current = false;
    if (hasErrors) {
      summaryRef.current?.focus();
    }
  }, [errors, formError, hasErrors, pending]);

  return (
    <form
      noValidate
      aria-busy={pending || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) {
          return;
        }
        awaitingResultRef.current = true;
        onSubmit();
      }}
      className={["flex flex-col gap-4", className ?? ""].filter(Boolean).join(" ")}
      {...rest}
    >
      {/* `noValidate` above: the browser's own constraint bubbles are
          inconsistent between engines, vanish on a timer, and are not
          reliably announced - the summary below is what carries validation
          messaging instead, so the two must not compete. */}
      <ErrorSummary
        ref={summaryRef}
        errors={fieldErrors}
        formError={formError}
        title={errorSummaryTitle}
      />
      {children}
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={pending}>
          {pending && pendingLabel ? pendingLabel : submitLabel}
        </Button>
        {secondaryActions}
      </div>
    </form>
  );
}
