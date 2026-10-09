import { useEffect, useRef, useState } from "react";

import { usePropertyDefinitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { refusalDetail } from "../api/conflicts.ts";
import { Card } from "../components/card.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import type { SubmitOutcome } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { LiveRegion } from "../components/live-region.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import { CODE_FIELD_ID, CodeField } from "./code-field.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import type { CollisionWarning, DesignationWarning } from "./collision-notice.tsx";
import { formatPropertyValue } from "./format-property-value.ts";
import { propertyValidationFieldErrors } from "./property-form-errors.ts";
import { normaliseForComparison } from "./python-text.ts";
import {
  CONTROLS,
  RepeatableValues,
  groupFieldId,
  isEmptySlotValue,
  newSlotId,
} from "./property-controls/index.ts";
import type { PropertyValueSlot } from "./property-controls/index.ts";
import { describeRun } from "./run-save.ts";
import type { FieldChange, SaveRun } from "./run-save.ts";
import {
  AcknowledgeCollisionDialog,
  ReinstateSynonymDialog,
  RetireBindingDialog,
} from "./row-action-dialogs.tsx";
import { SaveSummary, WarningList } from "./save-summary.tsx";
import { statusLabelFor, statusToneFor } from "./status-options.ts";
import { SynonymFields } from "./synonym-fields.tsx";
import {
  ADD_CHANGE_ID,
  ADD_SYNONYMS_FIELD_ID,
  activeSynonyms,
  addTextError,
  addedTerms,
  amendChangeId,
  blankRows,
  hasSynonymChanges,
  initialSynonymRows,
  mergeSynonymRows,
  retireChangeId,
  synonymChanges,
  synonymFieldId,
} from "./synonym-state.ts";
import type { SynonymRow } from "./synonym-state.ts";
import { termLength } from "./term-length.ts";
import { useCodeSelection } from "./use-code-selection.ts";
import { useEntrySave } from "./use-entry-save.ts";

/**
 * The catalogue entry edit form (FR-04, FR-08, FR-09, FR-24, FR-26, FR-36,
 * FR-37, FR-38, FR-77, FR-85, FR-89).
 *
 * One form, one changelog note and one Save for the entry's RCPA Preferred
 * term, its SNOMED CT code, its RCPA Synonyms and every registry property.
 * Only the fields the editor changed are sent, one request each, in that
 * order (`useEntrySave`).
 *
 * **Generated, not hand-written.** Each property row comes from
 * `usePropertyDefinitions`, and `CONTROLS` picks its input from
 * `form_control.control`, so a definition added in the registry appears here
 * with no deployment (FR-09, ADR-0013). The one exception is the specimen
 * hint, carried over from the panel this form replaces (FR-89).
 *
 * **A deprecated property that holds a value stays visible and read-only**
 * (FR-11); one with no value is not shown.
 */

const SPECIMEN_KEY = "specimen";
const TERM_FIELD_ID = "entry-preferred-term";
const NOTE_FIELD_ID = "entry-edit-note";
const TERM_CHANGE_ID = "preferred_term";
const BINDING_CHANGE_ID = "binding";

type EntryDetail = components["schemas"]["EntryDetail"];
type PropertyDefinitionResponse = components["schemas"]["PropertyDefinitionResponse"];
type PropertyValue = components["schemas"]["PropertyValue"];
type PropertyCardinality = components["schemas"]["PropertyCardinality"];

interface ValueItem {
  value: unknown;
  justification: string | null;
}

interface PropertyRow {
  definition: PropertyDefinitionResponse;
  values: PropertyValue[];
  editable: boolean;
}

/** The row action whose dialog is open. */
type RowDialog =
  | { kind: "reinstate"; term: string }
  | { kind: "retire-binding"; code: string }
  | { kind: "acknowledge"; warning: CollisionWarning };

interface Baseline {
  term: string;
  values: Record<string, ValueItem[]>;
}

/**
 * What `pendingChanges` found: the field to mark if the server refuses it, and
 * the render index of each property value it will send.
 */
interface PendingChange {
  change: FieldChange;
  fieldId: string | null;
  submittedIndexes: number[];
}

function buildRows(
  definitions: PropertyDefinitionResponse[],
  values: PropertyValue[],
): PropertyRow[] {
  const valuesByKey = new Map<string, PropertyValue[]>();
  for (const value of values) {
    const existing = valuesByKey.get(value.key);
    if (existing) {
      existing.push(value);
    } else {
      valuesByKey.set(value.key, [value]);
    }
  }

  return [...definitions]
    .sort((a, b) => a.display_order - b.display_order)
    .flatMap((definition) => {
      const rowValues = (valuesByKey.get(definition.key) ?? [])
        .slice()
        .sort((a, b) => a.ordinal - b.ordinal);
      const editable = definition.status === "active";
      if (!editable && rowValues.length === 0) {
        return [];
      }
      return [{ definition, values: rowValues, editable }];
    });
}

function activeCodeOf(entry: EntryDetail): string | null {
  return entry.bindings.find((binding) => binding.status === "active")?.code ?? null;
}

function retiredSynonyms(entry: EntryDetail): string[] {
  return entry.designations
    .filter((designation) => designation.status !== "active")
    .map((designation) => designation.term);
}

function toItem(value: PropertyValue): ValueItem {
  return { value: value.value, justification: value.justification };
}

function nonEmptySlotIndexes(slots: PropertyValueSlot[]): number[] {
  return slots.reduce<number[]>((indexes, slot, index) => {
    if (!isEmptySlotValue(slot.value)) {
      indexes.push(index);
    }
    return indexes;
  }, []);
}

/** An emptied justification box is the same as none. */
function justificationOf(slot: PropertyValueSlot): string | null {
  return slot.justification === null || slot.justification === ""
    ? null
    : slot.justification;
}

function sameValues(a: ValueItem[], b: ValueItem[]): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Built from what the entry holds, by property key, so the form needs no
 * registry definitions to start. A definition that arrives later only adds
 * rows to show.
 */
function initialState(entry: EntryDetail) {
  const valuesByKey: Record<string, PropertyValue[]> = {};
  for (const value of [...entry.properties].sort((a, b) => a.ordinal - b.ordinal)) {
    (valuesByKey[value.key] ??= []).push(value);
  }
  const baselineValues: Record<string, ValueItem[]> = {};
  const slots: Record<string, PropertyValueSlot[]> = {};
  for (const [key, values] of Object.entries(valuesByKey)) {
    baselineValues[key] = values.map(toItem);
    slots[key] = values.map((value) => ({
      id: newSlotId(),
      value: value.value,
      justification: value.justification,
    }));
  }
  const baseline: Baseline = { term: entry.preferred_term, values: baselineValues };
  return { baseline, slots };
}

export function EntryEditForm({ entry }: { entry: EntryDetail }) {
  const definitions = usePropertyDefinitions();
  const rows = definitions.data
    ? buildRows(definitions.data.items, entry.properties)
    : [];

  const [initial] = useState(() => initialState(entry));
  const [baseline, setBaseline] = useState<Baseline>(initial.baseline);
  const [term, setTerm] = useState(entry.preferred_term);
  const [slots, setSlots] = useState(initial.slots);
  const [synonyms, setSynonyms] = useState<SynonymRow[]>(() =>
    initialSynonymRows(activeSynonyms(entry.designations), newSlotId),
  );
  const [addText, setAddText] = useState("");
  const [pickedCode, setPickedCode] = useState<string | null>(null);
  const [dialog, setDialog] = useState<RowDialog | null>(null);
  const [dialogWarnings, setDialogWarnings] = useState<DesignationWarning[]>([]);
  const [acknowledged, setAcknowledged] = useState<ReadonlySet<string>>(new Set());
  const [seenEntry, setSeenEntry] = useState(entry);
  if (entry !== seenEntry) {
    // The entry was refetched: after this form's own save, or after a version
    // conflict. A field the editor has not touched must show what the entry now
    // holds, or the editor would later change a value they were never shown and
    // overwrite whoever last changed it (FR-38). A field they did change keeps
    // their value.
    setSeenEntry(entry);
    const next = initialState(entry);
    const keys = new Set([...Object.keys(slots), ...Object.keys(next.slots)]);
    const nextSlots = { ...slots };
    for (const key of keys) {
      const current = nonEmptySlotIndexes(slots[key] ?? []).map((index) => ({
        value: slots[key][index].value,
        justification: justificationOf(slots[key][index]),
      }));
      const fresh = next.baseline.values[key] ?? [];
      const untouched = sameValues(current, baseline.values[key] ?? []);
      if (untouched && !sameValues(current, fresh)) {
        nextSlots[key] = next.slots[key] ?? [];
      }
    }
    setSlots(nextSlots);
    if (
      normaliseForComparison(term) === normaliseForComparison(baseline.term) &&
      term !== entry.preferred_term
    ) {
      setTerm(entry.preferred_term);
    }
    setBaseline(next.baseline);
    setSynonyms(
      mergeSynonymRows(synonyms, activeSynonyms(entry.designations), newSlotId),
    );
    if (pickedCode !== null && activeCodeOf(entry) === pickedCode) {
      setPickedCode(null);
    }
  }
  const [serverLength, setServerLength] = useState<number | null>(null);
  const [refusals, setRefusals] = useState<Record<string, FormError[]>>({});
  const [ownErrors, setOwnErrors] = useState<FormError[]>([]);
  const [unexpected, setUnexpected] = useState<unknown>(null);
  const [run, setRun] = useState<SaveRun | null>(null);
  const [focusSummary, setFocusSummary] = useState(0);
  const summaryHeading = useRef<HTMLHeadingElement>(null);
  const changelogNote = useChangelogNote(NOTE_FIELD_ID);
  const { message, politeness, announce } = useAnnounce();
  const entrySave = useEntrySave(entry.business_key);
  const selection = useCodeSelection(pickedCode);
  const activeCode = activeCodeOf(entry);
  // "Wait for the code to finish checking" and a lookup failure describe the
  // lookup, which settles with no edit from the editor. A stored copy would
  // outlive the state it describes, so it goes when the lookup's state moves.
  const selectionState = `${selection.status}:${pickedCode ?? ""}`;
  const [seenSelectionState, setSeenSelectionState] = useState(selectionState);
  if (selectionState !== seenSelectionState) {
    setSeenSelectionState(selectionState);
    setOwnErrors((current) =>
      current.some((error) => error.fieldId === CODE_FIELD_ID)
        ? current.filter((error) => error.fieldId !== CODE_FIELD_ID)
        : current,
    );
  }
  // The entry is refetched after every run, but not before an editor can press
  // Save again, or open a row action. A run's own last version, and the term it
  // stored, are newer than the entry until that refetch lands, and sending the
  // older ones would make a write conflict with the editor's own previous one
  // (FR-38).
  const runIsNewer = run !== null && run.rowVersion > entry.row_version;
  const rowVersion = runIsNewer ? run.rowVersion : entry.row_version;

  useEffect(() => {
    if (focusSummary > 0) {
      summaryHeading.current?.focus();
    }
  }, [focusSummary]);

  function pendingChanges(): PendingChange[] {
    const found: PendingChange[] = [];
    if (normaliseForComparison(term) !== normaliseForComparison(baseline.term)) {
      found.push({
        change: {
          kind: "preferred_term",
          id: TERM_CHANGE_ID,
          label: "RCPA Preferred",
          currentTerm: baseline.term,
          newTerm: term,
        },
        fieldId: TERM_FIELD_ID,
        submittedIndexes: [],
      });
    }
    if (selection.status === "ready" && selection.concept.code !== activeCode) {
      found.push({
        change: {
          kind: "binding",
          id: BINDING_CHANGE_ID,
          label: "SNOMED CT code",
          currentCode: activeCode,
          concept: selection.concept,
        },
        fieldId: CODE_FIELD_ID,
        submittedIndexes: [],
      });
    }
    const synonymWork = synonymChanges(synonyms, addText);
    // Retirements go first: an editor who removes one synonym and gives its
    // text to another would otherwise have the rename refused as a duplicate
    // of a term that is still active.
    for (const { change, rowId } of synonymWork.retirements) {
      found.push({ change, fieldId: synonymFieldId(rowId), submittedIndexes: [] });
    }
    for (const { change, rowId } of synonymWork.amendments) {
      found.push({ change, fieldId: synonymFieldId(rowId), submittedIndexes: [] });
    }
    if (synonymWork.addition !== null) {
      found.push({
        change: synonymWork.addition,
        fieldId: ADD_SYNONYMS_FIELD_ID,
        submittedIndexes: [],
      });
    }
    for (const row of rows) {
      if (!row.editable) {
        continue;
      }
      const key = row.definition.key;
      const rowSlots = slots[key] ?? [];
      const submittedIndexes = nonEmptySlotIndexes(rowSlots);
      const values = submittedIndexes.map((index) => ({
        value: rowSlots[index].value,
        justification: justificationOf(rowSlots[index]),
      }));
      if (!sameValues(values, baseline.values[key] ?? [])) {
        found.push({
          change: {
            kind: "property",
            id: `property:${key}`,
            label: row.definition.label,
            key,
            values,
          },
          fieldId: null,
          submittedIndexes,
        });
      }
    }
    return found;
  }

  const changed = pendingChanges();
  const hasChanges =
    changed.length > 0 || pickedCode !== null || hasSynonymChanges(synonyms, addText);

  /** Drops the form's own errors for the fields being changed, and no others. */
  function clearOwnErrors(matches: (fieldId: string) => boolean) {
    setOwnErrors((current) =>
      current.some((error) => matches(error.fieldId))
        ? current.filter((error) => !matches(error.fieldId))
        : current,
    );
  }
  const noteGate = hasChanges && changelogNote.blocked;
  const blocked = !hasChanges || noteGate;

  function clearRefusalsWhere(matches: (id: string) => boolean) {
    setRefusals((current) => {
      if (!Object.keys(current).some(matches)) {
        return current;
      }
      return Object.fromEntries(Object.entries(current).filter(([key]) => !matches(key)));
    });
  }

  function clearRefusal(id: string) {
    clearRefusalsWhere((key) => key === id);
  }

  function validateOwnFields(): FormError[] {
    const found: FormError[] = [];
    if (normaliseForComparison(term) === "") {
      found.push({ fieldId: TERM_FIELD_ID, message: "Enter the preferred term." });
    }
    if (selection.status === "checking") {
      found.push({
        fieldId: CODE_FIELD_ID,
        message: "Wait for the code to finish checking before saving.",
      });
    } else if (selection.status === "unresolved") {
      found.push({ fieldId: CODE_FIELD_ID, message: selection.message });
    }
    for (const row of blankRows(synonyms)) {
      found.push({
        fieldId: synonymFieldId(row.id),
        message: "Enter the synonym, or remove it.",
      });
    }
    const tooMany = addTextError(addText);
    if (tooMany !== null) {
      found.push({ fieldId: ADD_SYNONYMS_FIELD_ID, message: tooMany });
    }
    return found;
  }

  function applyRun(result: SaveRun, sent: PendingChange[]) {
    const nextRefusals: Record<string, FormError[]> = {};
    let savedTermBaseline: string | null = null;
    const savedValues: Record<string, ValueItem[]> = {};
    const settled: { change: FieldChange; savedTerm: string | null }[] = [];
    let storedTerm: { sent: string; saved: string } | null = null;
    for (const outcome of result.outcomes) {
      const { change } = outcome;
      if (outcome.status === "saved") {
        if (change.kind === "preferred_term") {
          savedTermBaseline = outcome.savedTerm ?? change.newTerm;
          if (outcome.length !== null) {
            setServerLength(outcome.length);
          }
          if (outcome.savedTerm !== null) {
            storedTerm = { sent: change.newTerm, saved: outcome.savedTerm };
          }
        } else if (change.kind === "property") {
          savedValues[change.key] = change.values;
        } else {
          settled.push({ change, savedTerm: outcome.savedTerm });
        }
        continue;
      }
      if (outcome.status === "not-sent") {
        continue;
      }
      const reason =
        outcome.status === "failed"
          ? "Not saved. See the summary below."
          : outcome.message;
      if (change.kind !== "property") {
        const fieldId = sent.find((item) => item.change.id === change.id)?.fieldId;
        nextRefusals[change.id] = [
          { fieldId: fieldId ?? TERM_FIELD_ID, message: reason },
        ];
      } else {
        const indexes =
          sent.find((item) => item.change.id === change.id)?.submittedIndexes ?? [];
        const mapped =
          outcome.status === "refused"
            ? propertyValidationFieldErrors(change.key, outcome.error, indexes)
            : [];
        nextRefusals[change.id] =
          mapped.length > 0
            ? mapped
            : [{ fieldId: groupFieldId(change.key), message: reason }];
      }
    }
    setBaseline((current) => ({
      term: savedTermBaseline ?? current.term,
      values: { ...current.values, ...savedValues },
    }));
    if (storedTerm !== null) {
      const { sent, saved } = storedTerm;
      setTerm((current) => (current === sent ? saved : current));
    }
    applySettled(settled);
    setRefusals(nextRefusals);
    setRun(result);
  }

  /**
   * Makes the code and synonym fields match what the run saved, so a second
   * Save before the entry is refetched does not send the same change twice.
   */
  function applySettled(settled: { change: FieldChange; savedTerm: string | null }[]) {
    for (const { change, savedTerm } of settled) {
      if (change.kind === "binding") {
        setPickedCode(null);
      } else if (change.kind === "synonym_amend") {
        setSynonyms((current) =>
          current.map((row) =>
            row.original === change.currentTerm
              ? {
                  ...row,
                  original: savedTerm ?? change.newTerm,
                  term: row.term === change.newTerm ? (savedTerm ?? row.term) : row.term,
                }
              : row,
          ),
        );
      } else if (change.kind === "synonym_retire") {
        setSynonyms((current) => current.filter((row) => row.original !== change.term));
      } else if (change.kind === "synonyms_add") {
        setAddText((current) =>
          JSON.stringify(addedTerms(current)) === JSON.stringify(change.terms)
            ? ""
            : current,
        );
      }
    }
  }

  async function submit(): Promise<SubmitOutcome> {
    const found = validateOwnFields();
    setOwnErrors(found);
    setUnexpected(null);
    if (found.length > 0) {
      return { ok: false };
    }
    // A warning from an earlier reinstatement describes that write, as the
    // summary's warnings describe the last run.
    setDialogWarnings([]);
    const sent = changed;
    const storedTerm = runIsNewer ? baseline.term : entry.preferred_term;
    try {
      const result = await entrySave.save({
        changes: sent.map(({ change }) =>
          change.kind === "preferred_term"
            ? { ...change, currentTerm: storedTerm }
            : change,
        ),
        note: changelogNote.note,
        rowVersion,
      });
      applyRun(result, sent);
      announce(describeRun(result));
      if (result.outcomes.every((outcome) => outcome.status === "saved")) {
        changelogNote.reset();
      } else {
        setFocusSummary((count) => count + 1);
      }
    } catch (error) {
      setUnexpected(error);
    }
    // Focus is managed above, so the form must not wait for an error summary.
    return { ok: true };
  }

  const termErrors = [
    ...ownErrors.filter((error) => error.fieldId === TERM_FIELD_ID),
    ...(refusals[TERM_CHANGE_ID] ?? []),
  ];
  const codeError = [
    ...ownErrors.filter((error) => error.fieldId === CODE_FIELD_ID),
    ...(refusals[BINDING_CHANGE_ID] ?? []),
  ][0]?.message;
  const addError = [
    ...ownErrors.filter((error) => error.fieldId === ADD_SYNONYMS_FIELD_ID),
    ...(refusals[ADD_CHANGE_ID] ?? []),
  ][0]?.message;
  const synonymRowErrors: Record<string, string> = {};
  for (const row of synonyms) {
    const message = [
      ...ownErrors.filter((error) => error.fieldId === synonymFieldId(row.id)),
      ...(refusals[amendChangeId(row.original)] ?? []),
      ...(refusals[retireChangeId(row.original)] ?? []),
    ][0]?.message;
    if (message !== undefined) {
      synonymRowErrors[row.id] = message;
    }
  }
  const liveLength = termLength(term);

  return (
    <section aria-labelledby="entry-form-heading">
      <h2 id="entry-form-heading">Edit entry</h2>
      <LiveRegion message={message} politeness={politeness} />

      <Card>
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-3">
          <dt className="text-[var(--color-text-muted)]">Identifier</dt>
          <dd className="m-0 font-mono">{entry.business_key}</dd>
          <dt className="text-[var(--color-text-muted)]">Entry status</dt>
          <dd className="m-0">
            <StatusBadge
              tone={statusToneFor(entry.status)}
              label={statusLabelFor(entry.status)}
            />
          </dd>
          <dt className="text-[var(--color-text-muted)]">Last changed</dt>
          <dd className="m-0 tabular-nums">
            {new Date(entry.updated_at).toLocaleString()}
          </dd>
        </dl>
      </Card>

      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={entrySave.isPending}
        errors={ownErrors}
        formError={unexpected === null ? undefined : <RefusalNotice error={unexpected} />}
        submitBlocked={blocked}
        blockedReason={
          hasChanges ? changelogNote.blockedReason : "There are no changes to save."
        }
        blockedFieldId={hasChanges ? changelogNote.fieldId : undefined}
        onSubmitBlocked={() => {
          changelogNote.markSubmitAttempted();
          setOwnErrors(validateOwnFields());
        }}
        onSubmit={submit}
      >
        <Field
          id={TERM_FIELD_ID}
          label="RCPA Preferred"
          hint={
            <>
              The catalogue&rsquo;s own preferred term for this entry. Length:{" "}
              {liveLength} {liveLength === 1 ? "character" : "characters"}.
              {serverLength !== null &&
                ` The server counted ${serverLength} when you last saved.`}
            </>
          }
          error={termErrors[0]?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="text"
              value={term}
              onChange={(event) => {
                setTerm(event.target.value);
                clearOwnErrors((fieldId) => fieldId === TERM_FIELD_ID);
                clearRefusal(TERM_CHANGE_ID);
              }}
            />
          )}
        </Field>

        <CodeField
          bindings={entry.bindings}
          selection={selection}
          error={codeError}
          onPick={(code) => {
            clearRefusal(BINDING_CHANGE_ID);
            clearOwnErrors((fieldId) => fieldId === CODE_FIELD_ID);
            if (code === activeCode) {
              setOwnErrors((current) => [
                ...current,
                {
                  fieldId: CODE_FIELD_ID,
                  message: "That code is already bound to this entry.",
                },
              ]);
              return;
            }
            setPickedCode(code);
          }}
          onClear={() => {
            setPickedCode(null);
            clearOwnErrors((fieldId) => fieldId === CODE_FIELD_ID);
            clearRefusal(BINDING_CHANGE_ID);
          }}
          onRetire={(code) => setDialog({ kind: "retire-binding", code })}
        />

        <SynonymFields
          rows={synonyms}
          retiredTerms={retiredSynonyms(entry)}
          onReinstate={(term) => setDialog({ kind: "reinstate", term })}
          addText={addText}
          rowErrors={synonymRowErrors}
          addError={addError}
          onRowsChange={(next) => {
            const edited = next.filter(
              (row) => synonyms.find((old) => old.id === row.id) !== row,
            );
            const editedFieldIds = new Set(edited.map((row) => synonymFieldId(row.id)));
            const editedChangeIds = new Set(
              edited.flatMap((row) => [
                amendChangeId(row.original),
                retireChangeId(row.original),
              ]),
            );
            setSynonyms(next);
            clearOwnErrors((fieldId) => editedFieldIds.has(fieldId));
            clearRefusalsWhere((id) => editedChangeIds.has(id));
          }}
          onAddTextChange={(text) => {
            setAddText(text);
            clearOwnErrors((fieldId) => fieldId === ADD_SYNONYMS_FIELD_ID);
            clearRefusal(ADD_CHANGE_ID);
          }}
        />

        <h3>Registry properties</h3>
        {definitions.isPending && <p>Loading registry properties…</p>}
        {definitions.isError && (
          <p>
            {refusalDetail(definitions.error) ??
              "Registry properties could not be loaded. You can still edit the preferred term."}
          </p>
        )}
        {rows.map((row) => {
          const { definition } = row;
          if (!row.editable) {
            return (
              <dl
                key={definition.key}
                className="m-0 grid grid-cols-[max-content_1fr] gap-x-6"
              >
                <dt>{definition.label} (deprecated, read only)</dt>
                <dd className="m-0">
                  {row.values.map((value) => formatPropertyValue(value.value)).join(", ")}
                </dd>
              </dl>
            );
          }
          const Control = CONTROLS[definition.form_control.control];
          const changeId = `property:${definition.key}`;
          return (
            <div key={definition.key} className="flex flex-col gap-2">
              {definition.key === SPECIMEN_KEY ? (
                <p className="m-0 text-sm text-[var(--color-text-muted)]">
                  To record that a test accepts any specimen, choose Specimen (123038009)
                  on its own. A workbook import stores the same value as Any.
                </p>
              ) : null}
              <RepeatableValues
                propertyKey={definition.key}
                label={definition.label}
                cardinality={definition.cardinality as PropertyCardinality}
                control={Control}
                params={definition.form_control.params}
                slots={slots[definition.key] ?? []}
                onChange={(next) => {
                  setSlots((current) => ({ ...current, [definition.key]: next }));
                  clearRefusal(changeId);
                }}
                errors={refusals[changeId] ?? []}
              />
            </div>
          );
        })}
        {definitions.isSuccess && rows.length === 0 && (
          <p>This entry has no registry properties.</p>
        )}

        <ChangelogNoteField id={NOTE_FIELD_ID} changelogNote={changelogNote} />
      </Form>

      {run !== null && (
        <SaveSummary
          run={run}
          headingRef={summaryHeading}
          acknowledged={acknowledged}
          onAcknowledge={(warning) => setDialog({ kind: "acknowledge", warning })}
        />
      )}

      {dialogWarnings.length > 0 && (
        <section aria-labelledby="reinstated-warnings-heading">
          <h2 id="reinstated-warnings-heading">After reinstating a term</h2>
          <WarningList
            warnings={dialogWarnings}
            acknowledged={acknowledged}
            onAcknowledge={(warning) => setDialog({ kind: "acknowledge", warning })}
          />
        </section>
      )}

      {dialog?.kind === "reinstate" && (
        <ReinstateSynonymDialog
          key={dialog.term}
          businessKey={entry.business_key}
          rowVersion={rowVersion}
          term={dialog.term}
          onClose={() => setDialog(null)}
          onSaved={(warnings) => {
            setDialogWarnings(warnings);
            setDialog(null);
            announce(
              `Term reinstated.${
                warnings.length > 0
                  ? ` ${warnings.length} ${warnings.length === 1 ? "warning" : "warnings"} to review.`
                  : ""
              }`,
            );
          }}
        />
      )}
      {dialog?.kind === "retire-binding" && (
        <RetireBindingDialog
          key={dialog.code}
          businessKey={entry.business_key}
          rowVersion={rowVersion}
          code={dialog.code}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            announce("Code retired.");
          }}
        />
      )}
      {dialog?.kind === "acknowledge" && (
        <AcknowledgeCollisionDialog
          key={dialog.warning.term}
          businessKey={entry.business_key}
          warning={dialog.warning}
          onClose={() => setDialog(null)}
          onSaved={(acknowledgedTerm) => {
            setAcknowledged((current) => new Set([...current, acknowledgedTerm]));
            setDialog(null);
            announce("Duplicate acknowledged. It will not be reported again.");
          }}
        />
      )}
    </section>
  );
}
