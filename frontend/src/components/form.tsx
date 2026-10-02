import { useEffect, useRef, useState } from "react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { Button } from "./button.tsx";
import { ErrorSummary } from "./error-summary.tsx";
import type { FormError } from "./error-summary.tsx";

/** What a promise returned from `onSubmit` reports about the save. `ok: true`
 * says the save succeeded; anything else, including a value that is not an
 * outcome at all, is read as a failure. */
export type SubmitOutcome = { ok: boolean };

type FormProps = {
  /** Called once per accepted submit, already `preventDefault`-ed. Takes no
   * event: a caller that needed the event would be reaching around the one
   * submit path this component exists to provide.
   *
   * Returning nothing leaves the focus-move flag below armed until an error
   * prop arrives, which is right for a caller that derives `formError` from a
   * mutation hook: the error commits on a render after the request settles,
   * and the flag is still there to announce it.
   *
   * A returned promise must resolve a `SubmitOutcome`. `{ ok: true }` disarms
   * the flag without moving focus, so a screen that validates on change does
   * not pull focus out of the field being typed in once a save has
   * succeeded. `{ ok: false }` and a rejection both leave the flag armed, so
   * an error that commits several renders after the promise settles is still
   * announced. `Form` handles the rejection itself, so it never surfaces as
   * an unhandled rejection.
   *
   * The type is the guard against a `mutateAsync()` promise: it resolves the
   * saved record, not an outcome, so returning it does not compile. Map it to
   * an outcome instead - `.then(() => ({ ok: true }), () => ({ ok: false }))`. */
  onSubmit: () => void | Promise<SubmitOutcome>;
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
   * error before they have done anything. Documented as required whenever a
   * caller sets `submitBlocked` - omitting it falls back to a generic
   * message rather than announcing nothing at all (issue #62 review: a
   * blocked, attempted submit that announced nothing left the focus-move
   * effect armed with no error ever arriving to satisfy it). */
  blockedReason?: string;
  /** The id of the field `blockedReason` is about. Given, the reason is
   * rendered as a real summary link that moves focus to that field - the
   * same affordance every other field-level error gets (`ErrorSummary`'s
   * own contract). Omitted, it falls back to a plain, unlinked sentence,
   * the way `formError` already renders. */
  blockedFieldId?: string;
  /** Called when a submit is attempted while `submitBlocked` is true, in
   * place of `onSubmit` (which never runs in that case). Lets a caller with
   * its own additional field validation - computed inside `onSubmit` and
   * otherwise never recomputed while blocked - recompute and display it on
   * the same click, and mark its own field's guidance visible, the way a
   * blocked-but-attempted submit does for every other field (issue #62
   * review). Never called while `pending`; not called when `submitBlocked`
   * is false, since that path already reaches `onSubmit`. */
  onSubmitBlocked?: () => void;
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
  blockedFieldId,
  onSubmitBlocked,
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
  // Identifies each real submit so a promise-settle handler (below) can tell
  // whether it is answering the most recent submit or a stale one - a
  // caller that allows a second submit before the first promise settles
  // (possible if it never sets `pending`) must not have the first submit's
  // late settle disarm the second submit's still-armed flag. A ref, not
  // state: it only needs to be read back inside the settle handler and by
  // the effect below, never rendered.
  const submitIdRef = useRef(0);
  // The most recent submit whose promise resolved `{ ok: true }`, or
  // `undefined` before any has. Set only from that resolution - never
  // decided directly by the settle callback, which is a microtask that can
  // run before the effect below has even seen an error the same settle
  // caused the caller to set (see the effect's own comment for the race this
  // avoids).
  const [settledSubmitId, setSettledSubmitId] = useState<number | undefined>(undefined);
  // Whether the user has actually attempted a submit while blocked - the
  // gate is announced only from that point, never merely because the field
  // is currently empty (see `blockedReason`'s doc comment). Reset the
  // moment the gate itself lifts, not just on a fresh mount: without this,
  // a user who fixes the field, then reintroduces the same failure without
  // clicking submit again (backspacing a just-fixed note back to empty, say)
  // would see the reason reappear on its own - a second attempt has to
  // actually happen for it to be announced again.
  //
  // Adjusted during render rather than in an effect (React's own documented
  // "adjusting state when a prop changes" recipe): comparing against state
  // rather than a ref, since a ref may not be read or written during render.
  const [blockedAttempted, setBlockedAttempted] = useState(false);
  const [previousSubmitBlocked, setPreviousSubmitBlocked] = useState(submitBlocked);
  if (submitBlocked !== previousSubmitBlocked) {
    setPreviousSubmitBlocked(submitBlocked);
    if (!submitBlocked && blockedAttempted) {
      setBlockedAttempted(false);
    }
  }
  const showBlockedReason = submitBlocked && blockedAttempted;
  // A caller that sets `submitBlocked` without `blockedReason` is not
  // honouring the documented contract (see the prop's own doc comment), but
  // this fallback keeps a blocked, attempted submit from announcing nothing
  // at all - which would otherwise leave the focus-move effect below armed
  // forever, waiting for an error that will never arrive (issue #62 review).
  const announcedBlockedReason = blockedReason ?? "This field is not ready to submit.";
  const blockedFieldErrors: FormError[] =
    showBlockedReason && blockedFieldId
      ? [{ fieldId: blockedFieldId, message: announcedBlockedReason }]
      : [];
  // Linked to a field via `blockedFieldId` when the caller gives one - the
  // same summary-link affordance every other field error gets - and falls
  // back to a plain sentence (like `formError`) only when it does not. Both
  // render together when both are present: a rejected save (`formError`) and
  // a blocked gate are two different failures, and showing only one would
  // silently drop whichever `??` picked last (issue #62 review).
  const unlinkedBlockedReason =
    showBlockedReason && !blockedFieldId ? announcedBlockedReason : undefined;
  // Rebuilt every render - every caller passes `formError` as a freshly
  // constructed element, so memoising this would never hit. The focus-move
  // effect below still only re-focuses on a genuine new error, because it is
  // guarded by `awaitingResultRef.current`, not by this value being stable.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- see comment above
  const effectiveFormError =
    formError && unlinkedBlockedReason ? (
      <>
        {formError}
        <br />
        {unlinkedBlockedReason}
      </>
    ) : (
      (formError ?? unlinkedBlockedReason)
    );
  const fieldErrors = [...(errors ?? []), ...blockedFieldErrors];
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
  // submit at all still never moves focus. A caller that can say its save
  // succeeded resolves `{ ok: true }`, which disarms below instead of waiting
  // on an error that may never come. A caller that returns nothing, or
  // resolves `{ ok: false }`, relies entirely on an error arriving.
  //
  // The dependency array below is not what gates this effect: `effectiveFormError`
  // has a new identity every render (see its own comment above), so in practice
  // this runs on every render regardless of whether any of the four listed
  // dependencies actually changed. `awaitingResultRef.current` above is the only
  // real gate - treat the array as "what to re-check", not "when this runs".
  useEffect(() => {
    if (!awaitingResultRef.current) {
      return;
    }
    if (hasErrors) {
      // Takes priority over a settled promise: a rejected promise that also
      // set a form error still gets announced, whether or not the settle
      // below landed in the same render as this error.
      awaitingResultRef.current = false;
      summaryRef.current?.focus();
      return;
    }
    // A promise that resolved `{ ok: true }` disarms without moving focus -
    // this is what lets a validate-on-change screen use `Form`: the settle
    // handler never decides arm/disarm directly (that would race the error
    // this same settle may have just caused the caller to set - see the
    // settle handler's own comment), it only reports "a successful result
    // arrived for submit N" via `settledSubmitId`, and this effect - which
    // already runs once per render, after `hasErrors` is committed - is the
    // one place that decides. Guarded by matching submit id: a stale settle
    // from a superseded submit must not disarm the current one.
    //
    // Only success reaches here. A failure never records a settled id, so it
    // cannot disarm ahead of an error that commits on a later render - the
    // caller said the save failed, which is a promise that an error is
    // coming.
    if (settledSubmitId === submitIdRef.current) {
      awaitingResultRef.current = false;
    }
  }, [submitCount, errors, effectiveFormError, hasErrors, settledSubmitId]);

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
          onSubmitBlocked?.();
          awaitingResultRef.current = true;
          setSubmitCount((count) => count + 1);
          // Bumped here too, even though a blocked attempt always produces
          // an error today (`announcedBlockedReason`'s fallback guarantees
          // it, so `hasErrors` always wins before the settledSubmitId check
          // below is reached) - without it, a stale settledSubmitId left
          // over from the previous successful submit would still equal
          // `submitIdRef.current` during a blocked attempt, a latent
          // coupling to that guarantee rather than a guard that holds on
          // its own.
          ++submitIdRef.current;
          return;
        }
        awaitingResultRef.current = true;
        setSubmitCount((count) => count + 1);
        const thisSubmitId = ++submitIdRef.current;
        const result = onSubmit();
        // Checked explicitly, rather than wrapped in `Promise.resolve(...)`:
        // that would make the void case start disarming itself a tick
        // later too, silently regaining the always-armed contract's
        // opposite bug for every caller that never returns a promise.
        if (result instanceof Promise) {
          result.then(
            (outcome) => {
              // Read as `ok: true` or nothing: a resolved value that is not an
              // outcome must fail toward staying armed, since an unannounced
              // error is the defect and a stale armed flag is not.
              if ((outcome as SubmitOutcome | undefined)?.ok === true) {
                setSettledSubmitId(thisSubmitId);
              }
            },
            () => {
              // Handled here so a rejecting `onSubmit` is never an unhandled
              // rejection; the flag stays armed, like `{ ok: false }`.
            },
          );
        }
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
