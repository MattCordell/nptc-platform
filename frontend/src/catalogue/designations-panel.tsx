import { useState } from "react";

import type { CollisionWarning } from "./collision-notice.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
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

/**
 * The languages this screen offers. A one-element list rather than a free-text
 * field: `nptc_shared.language` checks BCP-47 *syntax* only and has no
 * registry, so an open control would accept `xx-ZZ` as readily as `mi-NZ`.
 * Widening this is a one-line change if the catalogue ever needs it.
 */
const SUPPORTED_LANGUAGES = ["en-AU"] as const;
const DEFAULT_LANGUAGE = SUPPORTED_LANGUAGES[0];

/** A row in the terms table - a real designation, or the entry's own term. */
interface TermRow {
  term: string;
  use: string;
  language: string;
  status: string;
  length: number;
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
    status: entry.status === "withdrawn" ? "withdrawn" : "active",
    // FR-85: the published figure, computed by the server from the stored
    // term. Never recomputed here - `CatalogueEntry.length` counts the term
    // *after* whitespace cleaning, so a browser-side `term.length` would
    // disagree with the catalogue for exactly the terms PRD Appendix A.1 is
    // about.
    length: entry.length,
    isEntryPreferredTerm: true,
  };
  const designations = entry.designations.map((designation: Designation) => ({
    ...designation,
    isEntryPreferredTerm: false,
  }));
  return [preferred, ...designations];
}

const NOTE_HINT =
  "This becomes the published History text, so describe the change - single words " +
  "like “update” or “fix” are not accepted.";

/** Client-side check only for emptiness; FR-37's substance is the server's. */
function noteError(note: string, fieldId: string): FormError[] {
  return note.trim().length === 0
    ? [{ fieldId, message: "Enter a changelog note describing this change." }]
    : [];
}

export function DesignationsPanel({ entry }: { entry: EntryDetail }) {
  const businessKey = entry.business_key;
  const [warnings, setWarnings] = useState<CollisionWarning[]>([]);
  const [editing, setEditing] = useState<TermRow | null>(null);
  const [retiring, setRetiring] = useState<TermRow | null>(null);
  const [acknowledging, setAcknowledging] = useState<CollisionWarning | null>(null);
  const { message, politeness, announce } = useAnnounce();

  const rows = termRows(entry);

  return (
    <section aria-labelledby="designations-heading">
      <h2 id="designations-heading">Terms</h2>
      <p>
        Every term this entry publishes, one row each. The preferred term is the
        catalogue&rsquo;s own; the rest are synonyms.
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
            render: (row) => (
              <span className="flex gap-2">
                {row.status === "active" && (
                  <Button
                    type="button"
                    variant="secondary"
                    // Named for the row, not just "Edit": a screen-reader user
                    // moving button to button hears which term each one acts
                    // on. `aria-label` rather than visually-hidden text
                    // because the accessible-name algorithm trims each node
                    // before joining, so "Edit" + " Ferritin" computes as
                    // "EditFerritin". The visible word is a prefix of the
                    // label, which is what WCAG 2.5.3 asks for.
                    aria-label={`Edit ${row.term}`}
                    onClick={() => setEditing(row)}
                  >
                    Edit
                  </Button>
                )}
                {/* No retire action on the entry's own preferred term:
                    `catalogue_entry.preferred_term` is NOT NULL and no route
                    retires it (ADR-0022). Offering a button that could only
                    ever fail would be worse than not offering one. */}
                {row.status === "active" && !row.isEntryPreferredTerm && (
                  <Button
                    type="button"
                    variant="danger"
                    aria-label={`Retire ${row.term}`}
                    onClick={() => setRetiring(row)}
                  >
                    Retire
                  </Button>
                )}
              </span>
            ),
          },
        ]}
        rows={rows}
        getRowKey={(row) => `${row.language}:${row.use}:${row.term}`}
        emptyState="This entry has no terms."
      />

      <AddSynonymsForm
        businessKey={businessKey}
        onSaved={(created, newWarnings) => {
          setWarnings(newWarnings);
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
          entry={entry}
          row={editing}
          onClose={() => setEditing(null)}
          onSaved={(newWarnings) => {
            setEditing(null);
            setWarnings(newWarnings);
            announce("Term saved.");
          }}
        />
      )}

      {retiring !== null && (
        <RetireDialog
          businessKey={businessKey}
          row={retiring}
          onClose={() => setRetiring(null)}
          onSaved={() => {
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
  onSaved,
}: {
  businessKey: string;
  onSaved: (created: number, warnings: CollisionWarning[]) => void;
}) {
  const [cell, setCell] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const add = useAddDesignations(businessKey);

  const terms = splitSynonyms(cell);

  return (
    <Form
      submitLabel="Add terms"
      pendingLabel="Adding"
      pending={add.isPending}
      errors={errors}
      formError={add.isError ? <RefusalNotice error={add.error} /> : undefined}
      errorSummaryHeadingLevel={3}
      onSubmit={() => {
        const found: FormError[] = [
          ...(terms.length === 0
            ? [
                {
                  fieldId: "add-terms",
                  message:
                    "Enter at least one term. A cell of only delimiters adds nothing.",
                },
              ]
            : []),
          ...noteError(note, "add-note"),
        ];
        setErrors(found);
        if (found.length > 0) {
          return;
        }
        add.mutate(
          {
            language: DEFAULT_LANGUAGE,
            terms,
            use: "synonym",
            reason: note,
          },
          {
            onSuccess: (result) => {
              setCell("");
              setNote("");
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
          silently dropped somewhere the editor cannot check. */}
      <p aria-live="polite">
        {cell.trim().length === 0
          ? "Nothing to add yet."
          : `This will add ${terms.length} ${terms.length === 1 ? "term" : "terms"}: ${terms
              .map((term) => `“${term}”`)
              .join(", ")}`}
      </p>
      <Field
        id="add-note"
        label="Changelog note"
        hint={NOTE_HINT}
        error={errors.find((error) => error.fieldId === "add-note")?.message}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        )}
      </Field>
    </Form>
  );
}

function AmendDialog({
  entry,
  row,
  onClose,
  onSaved,
}: {
  entry: EntryDetail;
  row: TermRow;
  onClose: () => void;
  onSaved: (warnings: CollisionWarning[]) => void;
}) {
  const [newTerm, setNewTerm] = useState(row.term);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const amend = useAmendDesignation(entry.business_key);

  return (
    <Dialog open onClose={onClose} title={`Edit ${row.term}`}>
      <Form
        submitLabel="Save term"
        pendingLabel="Saving"
        pending={amend.isPending}
        errors={errors}
        formError={amend.isError ? <RefusalNotice error={amend.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found: FormError[] = [
            ...(newTerm.trim().length === 0
              ? [{ fieldId: "amend-term", message: "Enter the term this should become." }]
              : []),
            ...noteError(note, "amend-note"),
          ];
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
              use: row.isEntryPreferredTerm ? "preferred" : "synonym",
              // FR-38, sent on both branches: required when this addresses
              // the entry's own term, honoured (not discarded) when it does
              // not. One code path, and no save that skips the lock.
              expected_row_version: entry.row_version,
              reason: note,
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
        <Field
          id="amend-note"
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === "amend-note")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
      </Form>
    </Dialog>
  );
}

function RetireDialog({
  businessKey,
  row,
  onClose,
  onSaved,
}: {
  businessKey: string;
  row: TermRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const retire = useRetireDesignation(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Retire ${row.term}`}>
      <Form
        submitLabel="Retire term"
        pendingLabel="Retiring"
        pending={retire.isPending}
        errors={errors}
        formError={retire.isError ? <RefusalNotice error={retire.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = noteError(note, "retire-note");
          setErrors(found);
          if (found.length > 0) {
            return;
          }
          retire.mutate(
            { language: row.language, term: row.term, reason: note },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <p>
          Retiring keeps the term and its history on the entry, marked retired. It is not
          deleted, and it stops being published.
        </p>
        <Field
          id="retire-note"
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === "retire-note")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
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
  warnings: CollisionWarning[];
  onAcknowledge: (warning: CollisionWarning) => void;
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
  warning: CollisionWarning;
  onClose: () => void;
  onSaved: (term: string) => void;
}) {
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const acknowledge = useAcknowledgeCollision(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Acknowledge ${warning.term}`}>
      <Form
        submitLabel="Acknowledge"
        pendingLabel="Acknowledging"
        pending={acknowledge.isPending}
        errors={errors}
        formError={
          acknowledge.isError ? <RefusalNotice error={acknowledge.error} /> : undefined
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = noteError(note, "acknowledge-note");
          setErrors(found);
          if (found.length > 0) {
            return;
          }
          acknowledge.mutate(
            { language: DEFAULT_LANGUAGE, term: warning.term, reason: note },
            { onSuccess: () => onSaved(warning.term) },
          );
        }}
      >
        <p>
          &ldquo;{warning.term}&rdquo; is also on {warning.business_key} —{" "}
          {warning.preferred_term}. Acknowledging records that this is intended, on this
          entry, and stops it being reported here again.
        </p>
        <Field
          id="acknowledge-note"
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === "acknowledge-note")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
      </Form>
    </Dialog>
  );
}
