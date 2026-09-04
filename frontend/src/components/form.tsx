import { useEffect, useRef, useState } from "react";
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
  /** True while a submit is in flight. Drives the submit button's disabled
   * appearance, `aria-busy`, and the re-entry guard - it is *not* part of
   * the focus contract below, deliberately, so a caller that never sets it
   * still gets a summary announced when its refusal arrives. */
  pending?: boolean;
  /** True while the caller's own client-side gate (issue #62 - a missing or
   * invalid changelog note) refuses submission. Unlike `pending`, this *is*
   * part of the focus contract: an attempted submit while blocked is
   * treated the same as a validation failure, and `blockedReason` is
   * announced through the same summary path. The caller owns validation
   * ("guidance while typing" belongs on the field itself, gated on blur,
   * never here); `Form` owns only the one submit path that must refuse it
   * (ADR-0026). */
  submitBlocked?: boolean;
  /** Why submission is refused while `submitBlocked` is true. Shown in the
   * error summary only once the user actually attempts a submit - never
   * before, so an empty required field does not accuse the user of an
   * error before they have done anything. */
  blockedReason?: string;
  /** Required - `Form` renders its own submit button, so "one submit path"
   * is structural rather than a convention a screen has to remember. */
  submitLabel: string;
  pendingLabel?: string;
  /** Cancel and friends, rendered beside the submit button. */
  secondaryActions?: ReactNode;
  errorSummaryTitle?: string;
  /** Heading level for the error summary - see `ErrorSummary`. Give a form
   * inside a `Dialog` or a nested section the level its surroundings need. */
  errorSummaryHeadingLevel?: 2 | 3 | 4 | 5 | 6;
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
  submitBlocked = false,
  blockedReason,
  submitLabel,
  pendingLabel,
  secondaryActions,
  errorSummaryTitle,
  errorSummaryHeadingLevel,
  children,
  className,
  ...rest
}: FormProps) {
  const summaryRef = useRef<HTMLDivElement>(null);
  const awaitingResultRef = useRef(false);
  // Counts submits purely to give the effect below something that always
  // changes. Without it the effect is keyed on the error props alone, and a
  // caller holding a stable `errors` array - a memoised or module-level one -
  // would submit an invalid form a second time and get no announcement at
  // all, because nothing React can see changed between the two attempts.
  const [submitCount, setSubmitCount] = useState(0);
  // Whether the user has actually attempted a submit while blocked - the
  // gate is announced only from that point, never merely because the field
  // is currently empty (see `blockedReason`'s doc comment).
  const [blockedAttempted, setBlockedAttempted] = useState(false);
  const fieldErrors = errors ?? [];
  const showBlockedReason = submitBlocked && blockedAttempted && Boolean(blockedReason);
  const effectiveFormError = formError ?? (showBlockedReason ? blockedReason : undefined);
  const hasErrors = fieldErrors.length > 0 || Boolean(effectiveFormError);

  // Focus is moved in an effect, not in the submit handler, because the
  // errors are the caller's to compute: they arrive as props on the render
  // that follows the submit, and for a server refusal they may not arrive
  // for several. The flag is what distinguishes "these errors are the
  // answer to a submit the user just made" - worth interrupting them for -
  // from errors that were on screen all along.
  //
  // The flag is held until an error actually arrives, and is cleared only by
  // announcing one. It deliberately does not consult `pending`: an earlier
  // version cleared the flag on the first render where `pending` was false,
  // which silently dropped the announcement for every caller whose pending
  // state is not set synchronously inside `onSubmit` - a mutation hook that
  // flips `isPending` a tick later, an `onSubmit` that awaits before setting
  // state, or a caller that simply never passes `pending`. That is the
  // majority case and the failure was invisible.
  //
  // The cost is the other direction: a submit that succeeds leaves the form
  // still listening, so an error appearing later with no further submit does
  // take focus. That is the better way round - after a submit, an error is
  // far more likely to be its answer than not - and an error that follows no
  // submit at all still never moves focus. It does mean these primitives
  // assume validate-on-submit: a screen validating on *change* would pull
  // focus out of the input on every keystroke that produced an error. Issue
  // #214 tracks disarming on a settled `onSubmit` promise instead.
  useEffect(() => {
    if (!awaitingResultRef.current || !hasErrors) {
      return;
    }
    awaitingResultRef.current = false;
    summaryRef.current?.focus();
  }, [submitCount, errors, effectiveFormError, hasErrors]);

  return (
    <form
      // `rest` first, then this component's own contract: `noValidate`,
      // `aria-busy` and the submit handler are what `Form` promises, and a
      // caller must not be able to unpick them by passing an attribute -
      // the same ordering `Select` uses over `Field`'s wiring.
      {...rest}
      noValidate
      aria-busy={pending || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) {
          return;
        }
        if (submitBlocked) {
          setBlockedAttempted(true);
          awaitingResultRef.current = true;
          setSubmitCount((count) => count + 1);
          return;
        }
        awaitingResultRef.current = true;
        setSubmitCount((count) => count + 1);
        onSubmit();
      }}
      className={["flex flex-col gap-4", className ?? ""].filter(Boolean).join(" ")}
    >
      {/* `noValidate` above: the browser's own constraint bubbles are
          inconsistent between engines, vanish on a timer, and are not
          reliably announced - the summary below is what carries validation
          messaging instead, so the two must not compete. */}
      <ErrorSummary
        ref={summaryRef}
        errors={fieldErrors}
        formError={effectiveFormError}
        title={errorSummaryTitle}
        headingLevel={errorSummaryHeadingLevel}
      />
      {children}
      <div className="flex items-center gap-2">
        {/* `aria-disabled`, not `disabled`: a user who submitted from the
            keyboard has focus on this button, and `disabled` removes it from
            the tab order mid-save, dropping focus to <body> with nothing
            announced to explain it. The re-entry guard in `onSubmit` above
            is what actually refuses the second submit, so the button only
            needs to *say* it is unavailable - and an aria-disabled control
            stays focusable and stays announced. `Button` styles
            aria-disabled the same way it styles disabled, so there is
            nothing to reproduce here. `submitBlocked` (issue #62) joins
            `pending` here for the same reason. */}
        <Button type="submit" aria-disabled={pending || submitBlocked || undefined}>
          {pending && pendingLabel ? pendingLabel : submitLabel}
        </Button>
        {secondaryActions}
      </div>
    </form>
  );
}
