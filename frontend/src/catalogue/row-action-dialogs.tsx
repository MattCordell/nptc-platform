import {
  useAcknowledgeCollision,
  useReinstateDesignation,
  useRetireBinding,
} from "../api/queries.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import { Form } from "../components/form.tsx";
import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import type { CollisionWarning, DesignationWarning } from "./collision-notice.tsx";

/**
 * The three edits the edit form's one Save does not cover (FR-04, FR-05, FR-08).
 *
 * Each is a single decision about one row, so each opens its own dialog, takes
 * its own changelog note and writes at once. A reinstated synonym, a
 * collision confirmed as intended and a code retired with no successor are
 * not a field the editor changes and saves with the others.
 */

function CancelButton({ onClose }: { onClose: () => void }) {
  return (
    <Button type="button" variant="secondary" onClick={onClose}>
      Cancel
    </Button>
  );
}

export function ReinstateSynonymDialog({
  businessKey,
  rowVersion,
  term,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  term: string;
  onClose: () => void;
  onSaved: (warnings: DesignationWarning[]) => void;
}) {
  const changelogNote = useChangelogNote("reinstate-note");
  const reinstate = useReinstateDesignation(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Reinstate ${term}`}>
      <Form
        submitLabel="Reinstate term"
        pendingLabel="Reinstating"
        pending={reinstate.isPending}
        formError={
          reinstate.isError ? <RefusalNotice error={reinstate.error} /> : undefined
        }
        submitBlocked={changelogNote.blocked}
        blockedReason={changelogNote.blockedReason}
        blockedFieldId={changelogNote.fieldId}
        onSubmitBlocked={changelogNote.markSubmitAttempted}
        errorSummaryHeadingLevel={3}
        secondaryActions={<CancelButton onClose={onClose} />}
        onSubmit={() => {
          reinstate.mutate(
            { term, reason: changelogNote.note, expected_row_version: rowVersion },
            { onSuccess: (result) => onSaved(result.warnings) },
          );
        }}
      >
        <p>
          This publishes the term again. The row is the same one that was retired, so its
          history stays one continuous record.
        </p>
        <ChangelogNoteField id="reinstate-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

export function AcknowledgeCollisionDialog({
  businessKey,
  warning,
  onClose,
  onSaved,
}: {
  businessKey: string;
  warning: CollisionWarning;
  onClose: () => void;
  onSaved: (term: string) => void;
}) {
  const changelogNote = useChangelogNote("acknowledge-note");
  const acknowledge = useAcknowledgeCollision(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Acknowledge ${warning.term}`}>
      <Form
        submitLabel="Acknowledge"
        pendingLabel="Acknowledging"
        pending={acknowledge.isPending}
        formError={
          acknowledge.isError ? <RefusalNotice error={acknowledge.error} /> : undefined
        }
        submitBlocked={changelogNote.blocked}
        blockedReason={changelogNote.blockedReason}
        blockedFieldId={changelogNote.fieldId}
        onSubmitBlocked={changelogNote.markSubmitAttempted}
        errorSummaryHeadingLevel={3}
        secondaryActions={<CancelButton onClose={onClose} />}
        onSubmit={() => {
          acknowledge.mutate(
            { term: warning.term, reason: changelogNote.note },
            { onSuccess: () => onSaved(warning.term) },
          );
        }}
      >
        <p>
          &ldquo;{warning.term}&rdquo; is also on {warning.business_key} &mdash;{" "}
          {warning.preferred_term}. Acknowledging records that this is intended, on this
          entry, and stops it being reported here again.
        </p>
        <ChangelogNoteField id="acknowledge-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

/**
 * Retires the active code without a successor. Replacing a code is the picker's
 * job: only `/replacement` records which code took its place.
 */
export function RetireBindingDialog({
  businessKey,
  rowVersion,
  code,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  code: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const changelogNote = useChangelogNote("retire-binding-note");
  const retire = useRetireBinding(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Retire ${code}`}>
      <Form
        submitLabel="Retire code"
        pendingLabel="Retiring"
        pending={retire.isPending}
        formError={retire.isError ? <RefusalNotice error={retire.error} /> : undefined}
        submitBlocked={changelogNote.blocked}
        blockedReason={changelogNote.blockedReason}
        blockedFieldId={changelogNote.fieldId}
        onSubmitBlocked={changelogNote.markSubmitAttempted}
        errorSummaryHeadingLevel={3}
        secondaryActions={<CancelButton onClose={onClose} />}
        onSubmit={() => {
          retire.mutate(
            {
              code,
              body: { reason: changelogNote.note, expected_row_version: rowVersion },
            },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <p>
          This stops {code} being the entry&rsquo;s active code and leaves it with none.
          The code is not deleted. It stays listed as retired, with this reason. To move
          the entry to another code, choose a replacement instead.
        </p>
        <ChangelogNoteField id="retire-binding-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}
