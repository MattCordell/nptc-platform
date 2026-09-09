import { useState } from "react";

import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import type { CollisionWarning } from "./collision-notice.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import { MAX_TERMS_PER_BATCH } from "./limits.ts";
import { splitSynonyms } from "./split-synonyms.ts";
import {
  useAcknowledgeCollision,
  useAddDesignations,
  useAmendDesignation,
  useRetireDesignation,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
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
 * `use: "preferred"` this panel sends when amending that row, and the retire
 * action it does not offer on it.
 *
 * **en-AU only.** The catalogue needs no other language today, and because
 * `ck_designation_no_en_au_preferred` forbids an en-AU *preferred designation*,
 * every `designation` row here is a synonym by construction. That is why the
 * add form has no `use` control: there is nothing else it could create.
 */

type EntryDetail = components["schemas"]["EntryDetail"];
type Designation = components["schemas"]["Designation"];
type DesignationUse = components["schemas"]["DesignationUse"];

/**
 * The read model types `use` as a bare string; the write model types it as the
 * two values `ck_designation_use` allows. Narrowed at the boundary rather than
 * asserted, and anything unrecognised is treated as a synonym - the value the
 * constraint makes overwhelmingly likelier, and the one whose amendment is
 * harmless if the guess is wrong.
 */
function designationUse(value: string): DesignationUse {
  return value === "preferred" ? "preferred" : "synonym";
}

/**
 * The languages this screen offers. A one-element list rather than a free-text
 * field: `nptc_shared.language` checks BCP-47 *syntax* only and has no
 * registry, so an open control would accept `xx-ZZ` as readily as `mi-NZ`.
 * Widening this is a one-line change if the catalogue ever needs it.
 */
const SUPPORTED_LANGUAGES = ["en-AU"] as const;
const DEFAULT_LANGUAGE = SUPPORTED_LANGUAGES[0];

/**
 * A warning, plus the language of the write it came back from.
 *
 * `CollisionWarning` carries no language of its own, and acknowledging one
 * addresses `(entry, language, term)` - so the language has to travel with it
 * rather than be assumed at the point of acknowledgement (review finding 1).
 */
interface PendingWarning extends CollisionWarning {
  language: string;
}

/** A row in the terms table - a real designation, or the entry's own term. */
interface TermRow {
  term: string;
  use: string;
  language: string;
  length: number;
  status: string;
  /**
   * True for the entry's own en-AU preferred term. Drives the two places the
   * ADR-0022 split is visible: the `use` sent on amendment, and whether the
   * retire action is offered at all.
   */
  isEntryPreferredTerm: boolean;
}

function termRows(entry: EntryDetail): TermRow[] {
  const preferred: TermRow = {
    term: entry.preferred_term,
    use: "preferred",
    language: DEFAULT_LANGUAGE,
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
    use: designation.use,
    language: designation.language,
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
  const [warnings, setWarnings] = useState<PendingWarning[]>([]);
  const [editing, setEditing] = useState<TermRow | null>(null);
  const [retiring, setRetiring] = useState<TermRow | null>(null);
  const [acknowledging, setAcknowledging] = useState<PendingWarning | null>(null);
  const { message, politeness, announce } = useAnnounce();

  const rows = sortedTermRows(termRows(entry));

  return (
    <section aria-labelledby="designations-heading">
      <h2 id="designations-heading">Terms</h2>
      <p>
        Every term this entry has ever held, one row each, active terms first. The
        preferred term is the catalogue&rsquo;s own; the rest are synonyms. A retired term
        stays listed, marked Retired, as history rather than something the entry currently
        publishes.
      </p>

      <LiveRegion message={message} politeness={politeness} />

      <DataTable
        caption={`Terms on ${businessKey}`}
        columns={[
          { key: "term", header: "Term", isRowHeader: true, render: (row) => row.term },
          { key: "use", header: "Use", render: (row) => row.use },
          { key: "language", header: "Language", render: (row) => row.language },
          // FR-24/FR-85: rendered as text. There is no control here, in the
          // amend dialog, or on any other path - the figure is computed from
          // the preferred term and is not a thing anyone can type.
          { key: "length", header: "Length", render: (row) => row.length },
          { key: "status", header: "Status", render: (row) => row.status },
          {
            key: "actions",
            header: "Actions",
            render: (row) =>
              row.status === "active" ? (
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
              ) : null,
          },
        ]}
        rows={rows}
        // `(language, use, term)` is not unique on its own: it is unique only
        // among *active* designations (`ix_designation_no_duplicate_active_term`),
        // so a term added, retired and re-added leaves two retired rows
        // sharing all three - matching `bindings-panel.tsx`'s identical
        // `getRowKey` reasoning for `Binding`, which carries no id for the
        // same reason (NFR-04/NFR-26). `rows`' own stable order (from
        // `sortedTermRows`) is what disambiguates.
        getRowKey={(row) =>
          `${row.language}:${row.use}:${row.term}:${row.status}:${rows.indexOf(row)}`
        }
        emptyState="This entry has no terms."
      />

      <AddSynonymsForm
        businessKey={businessKey}
        rowVersion={entry.row_version}
        onSaved={(created, newWarnings) => {
          setWarnings(
            newWarnings.map((warning) => ({ ...warning, language: DEFAULT_LANGUAGE })),
          );
          announce(
            `${created} ${created === 1 ? "term" : "terms"} added.` +
              (newWarnings.length > 0
                ? ` ${newWarnings.length} possible duplicate${
                    newWarnings.length === 1 ? "" : "s"
                  } to review.`
                : ""),
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
            setWarnings(
              newWarnings.map((warning) => ({ ...warning, language: editing.language })),
            );
            setEditing(null);
            announce("Term saved.");
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
            // a term the entry no longer has (review finding 4).
            setWarnings((current) =>
              current.filter((warning) => warning.term !== retiring.term),
            );
            setRetiring(null);
            announce("Term retired.");
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
            setWarnings((current) => current.filter((warning) => warning.term !== term));
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
  onSaved: (created: number, warnings: CollisionWarning[]) => void;
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
            language: DEFAULT_LANGUAGE,
            terms,
            use: "synonym",
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
  onSaved: (warnings: CollisionWarning[]) => void;
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
              language: row.language,
              term: row.term,
              new_term: newTerm,
              // Which storage home `term` means. Sent on every amendment,
              // not just the preferred one: nothing forbids a synonym whose
              // comparison key equals its own entry's preferred term, and
              // without `use` the route resolves designations first - so an
              // unqualified request for either would silently move the other.
              //
              // The row's own value, never a ternary on
              // `isEntryPreferredTerm`. The read route serves an entry's
              // synonyms *and its non-en-AU preferred variants*, so a
              // `use: "preferred"` designation is a shape this table renders
              // today; hardcoding "synonym" for every non-entry row would
              // mis-address exactly the term `use` was added to reach
              // (review finding 1).
              use: designationUse(row.use),
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
              language: row.language,
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
            so the row stays in the table, marked Retired, rather than
            vanishing - what the editor sees here has to match that. */}
        <p>
          This stops the term being published. The row stays in the table, marked Retired:
          nothing is deleted, and the catalogue keeps the term, and the change, in the
          entry&rsquo;s history.
        </p>
        <ChangelogNoteField id="retire-note" changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}

/**
 * Warning-severity collisions (FR-05): the same term active on another live
 * entry. These ride back on a *successful* write - the save happened - so this
 * is a panel to work through, not a refusal.
 */
function WarningsPanel({
  warnings,
  onAcknowledge,
}: {
  warnings: PendingWarning[];
  onAcknowledge: (warning: PendingWarning) => void;
}) {
  return (
    <section aria-labelledby="collision-warnings-heading">
      <h3 id="collision-warnings-heading">Possible duplicates</h3>
      <p>
        These terms were saved, and they are also in use on another entry. That is allowed
        - two entries can legitimately share a synonym. Acknowledge one to confirm it is
        intended and stop it being reported on every save.
      </p>
      <ul>
        {warnings.map((warning) => (
          <li key={`${warning.term}-${warning.business_key}`}>
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
        ))}
      </ul>
    </section>
  );
}

function AcknowledgeDialog({
  businessKey,
  warning,
  onClose,
  onSaved,
}: {
  businessKey: string;
  warning: PendingWarning;
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
            // The language of the write this warning came back from, not an
            // assumed default: an acknowledgement addresses
            // `(entry, language, term)` (review finding 1).
            {
              language: warning.language,
              term: warning.term,
              reason: changelogNote.note,
            },
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
