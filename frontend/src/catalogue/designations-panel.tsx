import { useState } from "react";

import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import type { CollisionWarning, DesignationWarning } from "./collision-notice.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import { MAX_TERMS_PER_BATCH } from "./limits.ts";
import { splitSynonyms } from "./split-synonyms.ts";
import {
  useAcknowledgeCollision,
  useAddDesignations,
  useAmendDesignation,
  useReinstateDesignation,
  useRetireDesignation,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The designation editing panel (issue #149; FR-04, FR-05, FR-24, FR-36).
 *
 * **One list, two storage homes.** ADR-0022 keeps the catalogue's own en-AU
 * preferred term on `catalogue_entry.preferred_term` and everything else on
 * `designation`, but the write API's own premise is that a client should not
 * have to model that split - `POST .../designations/amendment` dispatches to
 * whichever home the term lives in. So the table below is one list of terms,
 * the preferred term first, and the split shows up in exactly two places: the
 * `target: "preferred_term"` this panel sends when amending that row, and the
 * retire action it does not offer on it.
 *
 * Every `designation` row is a synonym (ADR-0022), so the add form has no
 * control for what kind of term it creates: there is nothing else it could create.
 */

type EntryDetail = components["schemas"]["EntryDetail"];
type Designation = components["schemas"]["Designation"];

/**
 * How each warning class is counted in an announcement. A `Record` over `kind`,
 * so a class added to the union fails to compile until it has a name here.
 */
const WARNING_NAMES: Record<DesignationWarning["kind"], { one: string; many: string }> = {
  collision: { one: "possible duplicate", many: "possible duplicates" },
  length: { one: "length warning", many: "length warnings" },
};

/** The clause an announcement appends so every warning class is heard, not only duplicates. */
function warningSummary(warnings: DesignationWarning[]): string {
  const parts = (Object.keys(WARNING_NAMES) as DesignationWarning["kind"][]).flatMap(
    (kind) => {
      const count = warnings.filter((warning) => warning.kind === kind).length;
      const name = WARNING_NAMES[kind];
      return count === 0 ? [] : [`${count} ${count === 1 ? name.one : name.many}`];
    },
  );
  return parts.length > 0 ? ` ${parts.join(" and ")} to review.` : "";
}

/** True for the collision warning an acknowledgement or retirement is about. */
function isCollisionOn(warning: DesignationWarning, term: string): boolean {
  return warning.kind === "collision" && warning.term === term;
}

/** A row in the terms table - a real designation, or the entry's own term. */
interface TermRow {
  term: string;
  use: "preferred" | "synonym";
  length: number;
  status: string;
  /**
   * True for the entry's own preferred term. Drives the two places the
   * ADR-0022 split is visible: the `target` sent on amendment, and whether the
   * retire action is offered at all.
   */
  isEntryPreferredTerm: boolean;
}

function termRows(entry: EntryDetail): TermRow[] {
  const preferred: TermRow = {
    term: entry.preferred_term,
    use: "preferred",
    // FR-85: the published figure, computed by the server from the stored
    // term. Never recomputed here - `CatalogueEntry.length` counts the term
    // *after* whitespace cleaning, so a browser-side `term.length` would
    // disagree with the catalogue for exactly the terms PRD Appendix A.1 is
    // about.
    length: entry.length,
    // `catalogue_entry.preferred_term` is `NOT NULL` and no route retires it
    // (ADR-0022) - this row is always active.
    status: "active",
    isEntryPreferredTerm: true,
  };
  const designations = entry.designations.map((designation: Designation) => ({
    term: designation.term,
    use: "synonym" as const,
    length: designation.length,
    status: designation.status,
    isEntryPreferredTerm: false,
  }));
  return [preferred, ...designations];
}

/**
 * Active first, then retired (issue #239) - matching `bindings-panel.tsx`'s
 * own `sortedBindings`, so the two panels read as one system.
 */
function sortedTermRows(rows: TermRow[]): TermRow[] {
  const active = rows.filter((row) => row.status === "active");
  const retired = rows.filter((row) => row.status !== "active");
  return [...active, ...retired];
}

export function DesignationsPanel({ entry }: { entry: EntryDetail }) {
  const businessKey = entry.business_key;
  // Scoped to one entry by the `key` this component is mounted under
  // (`admin-catalogue-edit.tsx`), so navigating from one entry's edit screen
  // to another's cannot carry the first entry's warnings across (review
  // finding 4).
  const [warnings, setWarnings] = useState<DesignationWarning[]>([]);
  const [editing, setEditing] = useState<TermRow | null>(null);
  const [retiring, setRetiring] = useState<TermRow | null>(null);
  const [reinstating, setReinstating] = useState<TermRow | null>(null);
  const [acknowledging, setAcknowledging] = useState<CollisionWarning | null>(null);
  const { message, politeness, announce } = useAnnounce();

  const rows = sortedTermRows(termRows(entry));

  return (
    <section aria-labelledby="designations-heading">
      <h2 id="designations-heading">Terms</h2>
      <p>
        Every term this entry has ever held, one row each, active terms first. The
        preferred term is the catalogue&rsquo;s own; the rest are synonyms. A retired term
        stays listed, marked retired in the Status column, as history rather than
        something the entry currently publishes.
      </p>

      <LiveRegion message={message} politeness={politeness} />

      <DataTable
        caption={`Terms on ${businessKey}`}
        columns={[
          { key: "term", header: "Term", isRowHeader: true, render: (row) => row.term },
          { key: "use", header: "Use", render: (row) => row.use },
          // FR-24/FR-85: rendered as text. There is no control here, in the
          // amend dialog, or on any other path - the figure is computed from
          // the preferred term and is not a thing anyone can type.
          { key: "length", header: "Length", render: (row) => row.length },
          { key: "status", header: "Status", render: (row) => row.status },
          {
            key: "actions",
            header: "Actions",
            render: (row) => {
              if (row.status === "active") {
                return (
                  <span className="flex gap-2">
                    {/* Named for the row, not just "Edit": a screen-reader user
                        moving button to button hears which term each one acts on,
                        and the use as well as the term - an entry can hold a
                        synonym whose comparison key equals its own preferred term
                        (the state #227's `use` exists for), and "Edit Ferritin"
                        twice over is two buttons a screen-reader user cannot tell
                        apart. `aria-label` rather than visually-hidden text
                        because the accessible-name algorithm trims each node
                        before joining, so "Edit" + " Ferritin" computes as
                        "EditFerritin". The visible word is a prefix of the label,
                        which is what WCAG 2.5.3 asks for. */}
                    <Button
                      type="button"
                      variant="secondary"
                      aria-label={`Edit ${row.term} (${row.use})`}
                      onClick={() => setEditing(row)}
                    >
                      Edit
                    </Button>
                    {/* No retire action on the entry's own preferred term:
                        `catalogue_entry.preferred_term` is NOT NULL and no route
                        retires it (ADR-0022). Offering a button that could only
                        ever fail would be worse than not offering one. */}
                    {!row.isEntryPreferredTerm && (
                      <Button
                        type="button"
                        variant="danger"
                        aria-label={`Retire ${row.term} (${row.use})`}
                        onClick={() => setRetiring(row)}
                      >
                        Retire
                      </Button>
                    )}
                  </span>
                );
              }
              // Issue #313: a retired row offers Reinstate, and nothing else -
              // there is no route to edit or re-retire a row that is already
              // retired. `isEntryPreferredTerm` rows are always active
              // (`termRows`' own construction - the entry's own preferred term
              // has no retire route, ADR-0022), so this branch is only ever
              // reached for a real `designation` row.
              return (
                <Button
                  type="button"
                  variant="secondary"
                  aria-label={`Reinstate ${row.term} (${row.use})`}
                  onClick={() => setReinstating(row)}
                >
                  Reinstate
                </Button>
              );
            },
          },
        ]}
        rows={rows}
        // `(use, term)` is not unique on its own: it is unique only among
        // *active* designations (`ix_designation_no_duplicate_active_term`), so a
        // term added, retired and re-added leaves two retired rows sharing both -
        // matching `bindings-panel.tsx`'s identical `getRowKey` reasoning for
        // `Binding`, which carries no id for the same reason (NFR-04/NFR-26).
        // `rows`' own stable order (from `sortedTermRows`) is what disambiguates.
        getRowKey={(row) => `${row.use}:${row.term}:${row.status}:${rows.indexOf(row)}`}
        emptyState="This entry has no terms."
      />

      <AddSynonymsForm
        businessKey={businessKey}
        rowVersion={entry.row_version}
        onSaved={(created, newWarnings) => {
          setWarnings(newWarnings);
          announce(
            `${created} ${created === 1 ? "term" : "terms"} added.` +
              warningSummary(newWarnings),
          );
        }}
      />

      {warnings.length > 0 && (
        <WarningsPanel
          warnings={warnings}
          onAcknowledge={(warning) => setAcknowledging(warning)}
        />
      )}

      {editing !== null && (
        <AmendDialog
          businessKey={businessKey}
          rowVersion={entry.row_version}
          row={editing}
          onClose={() => setEditing(null)}
          onSaved={(newWarnings) => {
            setWarnings(newWarnings);
            setEditing(null);
            announce(`Term saved.${warningSummary(newWarnings)}`);
          }}
        />
      )}

      {retiring !== null && (
        <RetireDialog
          businessKey={businessKey}
          rowVersion={entry.row_version}
          row={retiring}
          onClose={() => setRetiring(null)}
          onSaved={() => {
            // A warning about the term just retired is moot, and leaving its
            // Acknowledge button in place would record an acknowledgement for
            // a term the entry no longer actively publishes - it stays on
            // the entry as a retired row (issue #239), but a warning about a
            // possible duplicate makes sense only for a term still live
            // (review finding 4).
            setWarnings((current) =>
              current.filter((warning) => !isCollisionOn(warning, retiring.term)),
            );
            setRetiring(null);
            announce("Term retired.");
          }}
        />
      )}

      {reinstating !== null && (
        <ReinstateDialog
          businessKey={businessKey}
          rowVersion={entry.row_version}
          row={reinstating}
          onClose={() => setReinstating(null)}
          onSaved={(newWarnings) => {
            setWarnings(newWarnings);
            setReinstating(null);
            announce(`Term reinstated.${warningSummary(newWarnings)}`);
          }}
        />
      )}

      {acknowledging !== null && (
        <AcknowledgeDialog
          businessKey={businessKey}
          warning={acknowledging}
          onClose={() => setAcknowledging(null)}
          onSaved={(term) => {
            // Drop it locally too. The server stops returning it on the next
            // write, but the panel is showing the *previous* write's answer
            // and would otherwise keep offering an Acknowledge button for
            // something already acknowledged.
            setWarnings((current) =>
              current.filter((warning) => !isCollisionOn(warning, term)),
            );
            setAcknowledging(null);
            announce("Duplicate acknowledged. It will not be reported again.");
          }}
        />
      )}
    </section>
  );
}

/**
 * Adding terms, including the case FR-04 exists for: a synonym cell pasted
 * straight out of the legacy workbook, delimiters and all.
 */
function AddSynonymsForm({
  businessKey,
  rowVersion,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  onSaved: (created: number, warnings: DesignationWarning[]) => void;
}) {
  const [cell, setCell] = useState("");
  const changelogNote = useChangelogNote("add-note");
  const [errors, setErrors] = useState<FormError[]>([]);
  const add = useAddDesignations(businessKey);

  const terms = splitSynonyms(cell);

  // Computed outside `onSubmit` (issue #62 review) so a blocked submit can
  // recompute and display these the same way a non-blocked one does -
  // `onSubmit` never runs while blocked, and this used to be the only place
  // that called `setErrors`.
  function ownFieldErrors(): FormError[] {
    return [
      ...(terms.length === 0
        ? [
            {
              fieldId: "add-terms",
              message: "Enter at least one term. A cell of only delimiters adds nothing.",
            },
          ]
        : []),
      ...(terms.length > MAX_TERMS_PER_BATCH
        ? [
            {
              fieldId: "add-terms",
              message:
                `This adds ${terms.length} terms, and at most ` +
                `${MAX_TERMS_PER_BATCH} can be added at once. Split the paste ` +
                "into smaller batches.",
            },
          ]
        : []),
    ];
  }

  return (
    <Form
      submitLabel="Add terms"
      pendingLabel="Adding"
      pending={add.isPending}
      errors={errors}
      formError={add.isError ? <RefusalNotice error={add.error} /> : undefined}
      submitBlocked={changelogNote.blocked}
      blockedReason={changelogNote.blockedReason}
      blockedFieldId={changelogNote.fieldId}
      onSubmitBlocked={() => {
        changelogNote.markSubmitAttempted();
        setErrors(ownFieldErrors());
      }}
      errorSummaryHeadingLevel={3}
      onSubmit={() => {
        const found = ownFieldErrors();
        setErrors(found);
        if (found.length > 0) {
          return;
        }
        add.mutate(
          {
            terms,
            reason: changelogNote.note,
            // FR-38 (issue #300): required now that a batch add bumps the
            // entry's counter as one write.
            expected_row_version: rowVersion,
          },
          {
            onSuccess: (result) => {
              setCell("");
              changelogNote.reset();
              onSaved(result.designations.length, result.warnings);
            },
          },
        );
      }}
    >
      <h3>Add synonyms</h3>
      <Field
        id="add-terms"
        label="Synonyms"
        hint={
          "Paste a cell straight from the spreadsheet, or type one term. Separate several " +
          "with a semicolon."
        }
        error={errors.find((error) => error.fieldId === "add-terms")?.message}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            className={INPUT_CLASSES}
            type="text"
            value={cell}
            onChange={(event) => setCell(event.target.value)}
          />
        )}
      </Field>
      {/* The preview is what makes the delimiter repair visible: pasting
          "Zovirax;;Cyclir" says two terms, not three and not one, so the
          empty part FR-04 is about is seen to have gone rather than being
          silently dropped somewhere the editor cannot check.

          Deliberately *not* a live region. It changes on every keystroke, so
          announcing it would interrupt a screen-reader user once per
          character while they were still typing. The count that matters is
          announced once, after the save, through the panel's `LiveRegion`. */}
      <p>
        {cell.trim().length === 0
          ? "Nothing to add yet."
          : `This will add ${terms.length} ${terms.length === 1 ? "term" : "terms"}: ${terms
              .map((term) => `“${term}”`)
              .join(", ")}`}
      </p>
      <ChangelogNoteField id="add-note" changelogNote={changelogNote} />
    </Form>
  );
}

function AmendDialog({
  businessKey,
  rowVersion,
  row,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  row: TermRow;
  onClose: () => void;
  onSaved: (warnings: DesignationWarning[]) => void;
}) {
  const [newTerm, setNewTerm] = useState(row.term);
  const changelogNote = useChangelogNote("amend-note");
  const [errors, setErrors] = useState<FormError[]>([]);
  const amend = useAmendDesignation(businessKey);

  // See `AddSynonymsForm`'s identical note (issue #62 review): computed
  // outside `onSubmit` so a blocked submit can recompute and display it too.
  function ownFieldErrors(): FormError[] {
    return [
      ...(newTerm.trim().length === 0
        ? [{ fieldId: "amend-term", message: "Enter the term this should become." }]
        : []),
    ];
  }

  return (
    <Dialog open onClose={onClose} title={`Edit ${row.term}`}>
      <Form
        submitLabel="Save term"
        pendingLabel="Saving"
        pending={amend.isPending}
        errors={errors}
        formError={amend.isError ? <RefusalNotice error={amend.error} /> : undefined}
        submitBlocked={changelogNote.blocked}
        blockedReason={changelogNote.blockedReason}
        blockedFieldId={changelogNote.fieldId}
        onSubmitBlocked={() => {
          changelogNote.markSubmitAttempted();
          setErrors(ownFieldErrors());
        }}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = ownFieldErrors();
          setErrors(found);
          if (found.length > 0) {
            return;
          }
          amend.mutate(
            {
              term: row.term,
              new_term: newTerm,
              // Which storage home `term` means. Sent on every amendment,
              // not just the preferred one: nothing forbids a synonym whose
              // comparison key equals its own entry's preferred term, and
              // without `target` the route resolves designations first - so an
              // unqualified request for either would silently move the other.
              target: row.isEntryPreferredTerm ? "preferred_term" : "synonym",
              // FR-38 (issue #300): required on both branches, unconditionally
              // - the backend rejects either without it. One code path, and
              // no save that skips the lock.
              expected_row_version: rowVersion,
              reason: changelogNote.note,
            },
            { onSuccess: (result) => onSaved(result.warnings) },
          );
        }}
      >
        <Field
          id="amend-term"
          label="Term"
          hint={
            row.isEntryPreferredTerm
              ? "This is the catalogue's own preferred term for this entry."
              : undefined
          }
          error={errors.find((error) => error.fieldId === "amend-term")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="text"
              value={newTerm}
              onChange={(event) => setNewTerm(event.target.value)}
            />
          )}
        </Field>
        <ChangelogNoteField id="amend-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

function RetireDialog({
  businessKey,
  rowVersion,
  row,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  row: TermRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const changelogNote = useChangelogNote("retire-note");
  const retire = useRetireDesignation(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Retire ${row.term}`}>
      <Form
        submitLabel="Retire term"
        pendingLabel="Retiring"
        pending={retire.isPending}
        formError={retire.isError ? <RefusalNotice error={retire.error} /> : undefined}
        submitBlocked={changelogNote.blocked}
        blockedReason={changelogNote.blockedReason}
        blockedFieldId={changelogNote.fieldId}
        onSubmitBlocked={changelogNote.markSubmitAttempted}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          retire.mutate(
            {
              term: row.term,
              reason: changelogNote.note,
              // FR-38 (issue #300): required now that retiring a term bumps
              // the entry's counter.
              expected_row_version: rowVersion,
            },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        {/* Issue #239: the admin read route now serves retired designations,
            so the row stays in the table, marked retired in the Status
            column, rather than vanishing - what the editor sees here has to
            match that. */}
        <p>
          This stops the term being published. The row stays in the table, marked retired
          in the Status column: nothing is deleted, and the catalogue keeps the term, and
          the change, in the entry&rsquo;s history.
        </p>
        <ChangelogNoteField id="retire-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

function ReinstateDialog({
  businessKey,
  rowVersion,
  row,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  row: TermRow;
  onClose: () => void;
  onSaved: (warnings: DesignationWarning[]) => void;
}) {
  const changelogNote = useChangelogNote("reinstate-note");
  const reinstate = useReinstateDesignation(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Reinstate ${row.term}`}>
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
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          reinstate.mutate(
            {
              term: row.term,
              reason: changelogNote.note,
              // FR-38 (issue #300): required, same as add/amend/retire.
              expected_row_version: rowVersion,
            },
            { onSuccess: (result) => onSaved(result.warnings) },
          );
        }}
      >
        {/* Issue #313: the same row goes active again, so its history reads
            as one continuous record across create, retire and reinstate -
            not a retirement paired with an unrelated-looking new row, which
            re-adding the term instead of reinstating it would produce. */}
        <p>
          This publishes the term again. The row is the same one that was retired: its
          history stays one continuous record, and it keeps its place in the entry&rsquo;s
          history.
        </p>
        <ChangelogNoteField id="reinstate-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

/**
 * Warnings that ride back on a *successful* write - the save happened - so this
 * is a panel to work through, not a refusal. One panel for every warning class
 * (ADR-0045): a new class adds a `kind` case to `WarningItem`, not a panel.
 */
function WarningsPanel({
  warnings,
  onAcknowledge,
}: {
  warnings: DesignationWarning[];
  onAcknowledge: (warning: CollisionWarning) => void;
}) {
  return (
    <section aria-labelledby="warnings-heading">
      <h3 id="warnings-heading">Check these terms</h3>
      <p>
        These changes were saved. Each warning below is worth a look, and none of them
        blocks the save.
      </p>
      {warnings.some((warning) => warning.kind === "collision") && (
        <p>
          A term can be on two entries, because two entries can legitimately share a
          synonym. Acknowledge a duplicate to confirm it is intended and stop it being
          reported on every save.
        </p>
      )}
      <ul>
        {warnings.map((warning) => (
          <WarningItem
            key={warningKey(warning)}
            warning={warning}
            onAcknowledge={onAcknowledge}
          />
        ))}
      </ul>
    </section>
  );
}

function warningKey(warning: DesignationWarning): string {
  switch (warning.kind) {
    case "collision":
      return `collision-${warning.term}-${warning.business_key}`;
    case "length":
      return "length";
    default: {
      const unhandled: never = warning;
      return unhandled;
    }
  }
}

function WarningItem({
  warning,
  onAcknowledge,
}: {
  warning: DesignationWarning;
  onAcknowledge: (warning: CollisionWarning) => void;
}) {
  switch (warning.kind) {
    case "collision":
      // FR-05: the same term active on another live entry. Allowed - two
      // entries can legitimately share a synonym - so the action is to confirm
      // it is intended.
      return (
        <li>
          <span>
            &ldquo;{warning.term}&rdquo; is also on {warning.business_key} —{" "}
            {warning.preferred_term}
          </span>{" "}
          <Button
            type="button"
            variant="secondary"
            aria-label={`Acknowledge ${warning.term}`}
            onClick={() => onAcknowledge(warning)}
          >
            Acknowledge
          </Button>
        </li>
      );
    case "length":
      // FR-86: the preferred term is over the configured maximum. It has no
      // action: shortening it is an ordinary edit. Like every warning here it
      // describes the last write, so the next add, amend or reinstate replaces it
      // whatever that write was.
      return (
        <li>
          The preferred term is {warning.length} characters long, which is over the
          maximum of {warning.max_length}. Consider shortening it.
        </li>
      );
    default: {
      const unhandled: never = warning;
      return unhandled;
    }
  }
}

function AcknowledgeDialog({
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
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          acknowledge.mutate(
            { term: warning.term, reason: changelogNote.note },
            { onSuccess: () => onSaved(warning.term) },
          );
        }}
      >
        <p>
          &ldquo;{warning.term}&rdquo; is also on {warning.business_key} —{" "}
          {warning.preferred_term}. Acknowledging records that this is intended, on this
          entry, and stops it being reported here again.
        </p>
        <ChangelogNoteField id="acknowledge-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}
