import { useState } from "react";

import { validateChangelogNote } from "./changelog-note.ts";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";

/**
 * The one changelog note field every edit form composes (issue #62). Replaces
 * the three copy-pasted `noteError`/`reasonError` helpers and both copies of
 * `NOTE_HINT` that used to live in `designations-panel.tsx`, `bindings-
 * panel.tsx` and `properties-panel.tsx`.
 */
const NOTE_HINT =
  "This becomes the published History text, so describe the change - single words " +
  'like "update" or "fix" are not accepted.';

export interface UseChangelogNoteResult {
  /** The id passed to `useChangelogNote` - ready for `Form`'s
   * `blockedFieldId`, so the summary's blocked-reason entry links to this
   * field the same way every other field error does. */
  fieldId: string;
  note: string;
  setNote: (note: string) => void;
  /** True unless the current note passes FR-37 - feeds `Form`'s
   * `submitBlocked`. */
  blocked: boolean;
  /** Which FR-37 rule is unmet, ready for `Form`'s `blockedReason` -
   * present whenever `blocked` is true, shown by `Form` only once a submit
   * is actually attempted. */
  blockedReason: string | undefined;
  /** The same message, but gated on the field having been blurred, or a
   * submit having been attempted while blocked, at least once - so a fresh,
   * empty field is not shouted at before the user has done anything
   * (ADR-0026's `Form` assumes validate-on-submit; routing this through
   * `errors` on every keystroke would pull focus out of the field). Render
   * this as the field's own inline text, never as a submit-time summary
   * entry. */
  guidance: string | undefined;
  /** For a caller that wants the note's failure to also appear as a linked
   * entry in the form's own error summary, keyed to this field's id.
   * Empty while the note is valid. */
  errors: FormError[];
  onBlur: () => void;
  /** Call unconditionally from `Form`'s `onSubmitBlocked` (never from
   * `onSubmit` - a non-blocked submit needs no extra prompt). Without this,
   * a submit attempted on a note the user never focused links the summary
   * to a field whose own error slot is empty - unlike every other
   * field-level error in this codebase (issue #62 review). */
  markSubmitAttempted: () => void;
  /** Clears the note, blur-tracking and attempted-submit tracking - call
   * after a successful save. */
  reset: () => void;
}

/** `fieldId` must match the id given to `ChangelogNoteField` below, so the
 * error summary's link and the field it names agree. */
export function useChangelogNote(fieldId: string): UseChangelogNoteResult {
  const [note, setNote] = useState("");
  const [blurred, setBlurred] = useState(false);
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const result = validateChangelogNote(note);
  const message = result.status === "ok" ? undefined : result.message;

  return {
    fieldId,
    note,
    setNote,
    blocked: result.status !== "ok",
    blockedReason: message,
    guidance: blurred || submitAttempted ? message : undefined,
    errors: message ? [{ fieldId, message }] : [],
    onBlur: () => setBlurred(true),
    markSubmitAttempted: () => setSubmitAttempted(true),
    reset: () => {
      setNote("");
      setBlurred(false);
      setSubmitAttempted(false);
    },
  };
}

export function ChangelogNoteField({
  id,
  changelogNote,
}: {
  id: string;
  changelogNote: UseChangelogNoteResult;
}) {
  return (
    <Field id={id} label="Changelog note" hint={NOTE_HINT} error={changelogNote.guidance}>
      {(controlProps) => (
        <input
          {...controlProps}
          type="text"
          value={changelogNote.note}
          onChange={(event) => changelogNote.setNote(event.target.value)}
          onBlur={changelogNote.onBlur}
        />
      )}
    </Field>
  );
}
