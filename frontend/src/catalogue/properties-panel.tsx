import { useState } from "react";

import { asPropertyValidationError, refusalDetail } from "../api/conflicts.ts";
import { usePatchEntryCore, usePropertyDefinitions, useSavePropertyValues } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { Checkbox } from "../components/checkbox.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { RefusalNotice } from "./collision-notice.tsx";
import {
  CONTROLS,
  RepeatableValues,
  groupFieldId,
  isEmptySlotValue,
  slotFieldId,
} from "./property-controls/index.ts";
import type { PropertyValueSlot } from "./property-controls/index.ts";

/**
 * The registry properties panel (issue #151; FR-09, FR-10, FR-11, FR-36,
 * FR-37, FR-38, FR-77, FR-88, FR-89) - the third and last sibling section on
 * the admin catalogue edit screen.
 *
 * **Generated, not hand-written.** Every row comes from `usePropertyDefinitions`
 * plus whatever `entry.properties` already holds for that key - adding a
 * property definition through the registry admin screens makes it appear
 * here with no deployment (FR-09), because nothing in this module names a
 * specific property key.
 *
 * **`form_control.control` picks the input, never `datatype`.** `CONTROLS`
 * (`property-controls/index.ts`) is the one place that dispatches on
 * `ControlKind`; this file only ever reads it, matching ADR-0013 SS3.
 *
 * **Per-property save**, matching `DesignationsPanel`/`BindingsPanel`: a
 * read-only table, an Edit dialog per property that replaces that property's
 * whole value set in one call plus one changelog note - `save_property_values`'
 * own whole-set-replace signature forces this shape.
 *
 * **A deprecated property with a recorded value stays visible, read-only**
 * (FR-11) - `buildRows` below is the one place that decides whether a
 * deprecated definition is shown at all: only when `entry.properties` still
 * holds a value for it, since FR-11 is about not losing what was already
 * recorded, not about surfacing every deprecated definition that ever
 * existed.
 *
 * **`specimen_unconstrained` is not a property value.** It lives on
 * `catalogue_entry` itself (issue #249) and is edited through its own small
 * dialog and its own write route (`usePatchEntryCore`), never through
 * `useSavePropertyValues` - FR-89's whole point is that "Any" and "no
 * specimen values recorded" are different states, and conflating their
 * writes would reintroduce exactly that ambiguity.
 */

type EntryDetail = components["schemas"]["EntryDetail"];
type PropertyDefinitionResponse = components["schemas"]["PropertyDefinitionResponse"];
type PropertyValue = components["schemas"]["PropertyValue"];
type PropertyCardinality = components["schemas"]["PropertyCardinality"];

interface PropertyRow {
  definition: PropertyDefinitionResponse;
  values: PropertyValue[];
  editable: boolean;
}

/**
 * Joins the registry's definitions to what this entry actually holds.
 *
 * Sorted by `display_order` (FR-09's own generation order). A deprecated
 * definition is included only when `entry.properties` still names it - see
 * the module docstring's FR-11 note - so a deprecated property nobody ever
 * recorded a value against simply never appears, matching "never offered
 * for entry" for the case where there is nothing to retain either.
 */
function buildRows(definitions: PropertyDefinitionResponse[], values: PropertyValue[]): PropertyRow[] {
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

function formatValues(values: PropertyValue[]): string {
  if (values.length === 0) {
    return "No value recorded.";
  }
  return values.map((value) => String(value.value)).join(", ");
}

const NOTE_HINT =
  "This becomes the published History text, so describe the change - single words " +
  "like “update” or “fix” are not accepted.";

function noteError(note: string, fieldId: string): FormError[] {
  return note.trim().length === 0
    ? [{ fieldId, message: "Enter a changelog note describing this change." }]
    : [];
}

export function PropertiesPanel({ entry }: { entry: EntryDetail }) {
  const businessKey = entry.business_key;
  const definitions = usePropertyDefinitions();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingSpecimenFlag, setEditingSpecimenFlag] = useState(false);
  const { message, politeness, announce } = useAnnounce();

  const rows = definitions.data ? buildRows(definitions.data.items, entry.properties) : [];
  const editingRow = rows.find((row) => row.definition.key === editingKey) ?? null;

  return (
    <section aria-labelledby="properties-heading">
      <h2 id="properties-heading">Registry properties</h2>
      <p>
        Every property this entry can record, generated from the current registry
        definitions. A deprecated property keeps its recorded value visible, but is no
        longer offered for new entry (FR-11).
      </p>

      <LiveRegion message={message} politeness={politeness} />

      {definitions.isPending && <p>Loading registry properties…</p>}
      {definitions.isError && (
        <p>
          {refusalDetail(definitions.error) ??
            "Registry properties could not be loaded. Try again."}
        </p>
      )}

      {definitions.data && (
        <DataTable
          caption={`Registry properties on ${businessKey}`}
          columns={[
            {
              key: "label",
              header: "Property",
              isRowHeader: true,
              render: (row) => row.definition.label,
            },
            {
              key: "cardinality",
              header: "Cardinality",
              render: (row) => row.definition.cardinality,
            },
            { key: "values", header: "Values", render: (row) => formatValues(row.values) },
            {
              key: "status",
              header: "Status",
              render: (row) => (row.editable ? "Active" : "Deprecated (retained)"),
            },
            {
              key: "actions",
              header: "Actions",
              render: (row) =>
                row.editable ? (
                  <Button
                    type="button"
                    variant="secondary"
                    aria-label={`Edit ${row.definition.label}`}
                    onClick={() => setEditingKey(row.definition.key)}
                  >
                    Edit
                  </Button>
                ) : null,
            },
          ]}
          rows={rows}
          getRowKey={(row) => row.definition.key}
          emptyState="This entry has no registry properties."
        />
      )}

      <SpecimenUnconstrainedSummary
        entry={entry}
        onEdit={() => setEditingSpecimenFlag(true)}
      />

      {editingRow && (
        <PropertyEditDialog
          businessKey={businessKey}
          rowVersion={entry.row_version}
          definition={editingRow.definition}
          values={editingRow.values}
          onClose={() => setEditingKey(null)}
          onSaved={() => {
            const label = editingRow.definition.label;
            setEditingKey(null);
            announce(`${label} saved.`);
          }}
        />
      )}

      {editingSpecimenFlag && (
        <SpecimenUnconstrainedDialog
          businessKey={businessKey}
          rowVersion={entry.row_version}
          current={entry.specimen_unconstrained}
          onClose={() => setEditingSpecimenFlag(false)}
          onSaved={() => {
            setEditingSpecimenFlag(false);
            announce("Specimen setting saved.");
          }}
        />
      )}
    </section>
  );
}

function SpecimenUnconstrainedSummary({
  entry,
  onEdit,
}: {
  entry: EntryDetail;
  onEdit: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <p>
        Accepts any specimen (<strong>Any</strong>):{" "}
        {entry.specimen_unconstrained ? "Yes" : "No"}. This is a core entry setting, not a
        property value (FR-89) - an entry cannot both hold recorded specimen codes and
        accept any.
      </p>
      <Button type="button" variant="secondary" onClick={onEdit}>
        Edit
      </Button>
    </div>
  );
}

function PropertyEditDialog({
  businessKey,
  rowVersion,
  definition,
  values,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  definition: PropertyDefinitionResponse;
  values: PropertyValue[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [slots, setSlots] = useState<PropertyValueSlot[]>(
    values.map((value) => ({ value: value.value, justification: value.justification })),
  );
  const [note, setNote] = useState("");
  const [clientErrors, setClientErrors] = useState<FormError[]>([]);
  const save = useSavePropertyValues(businessKey, definition.key);
  const Control = CONTROLS[definition.form_control.control];
  const noteFieldId = `${definition.key}-note`;

  const validation = asPropertyValidationError(save.error);
  const serverErrors: FormError[] =
    validation?.issues.map((issue) => ({
      fieldId:
        issue.ordinal === null
          ? groupFieldId(definition.key)
          : slotFieldId(definition.key, issue.ordinal),
      message: issue.message,
    })) ?? [];
  const errors = [...clientErrors, ...serverErrors];

  return (
    <Dialog open onClose={onClose} title={`Edit ${definition.label}`}>
      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={save.isPending}
        errors={errors}
        // The field-level 422 (`validation`) is already rendered per-field via
        // `errors` above; the generic slot only carries a refusal with no
        // field attribution (a rejected note, a write against a now-
        // deprecated property, FR-38's conflict).
        formError={save.isError && validation === null ? <RefusalNotice error={save.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = noteError(note, noteFieldId);
          setClientErrors(found);
          if (found.length > 0) {
            return;
          }
          const submitted = slots
            .filter((slot) => !isEmptySlotValue(slot.value))
            .map((slot) => ({ value: slot.value, justification: slot.justification }));
          save.mutate(
            { values: submitted, reason: note, expected_row_version: rowVersion },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <RepeatableValues
          propertyKey={definition.key}
          label={definition.label}
          cardinality={definition.cardinality as PropertyCardinality}
          control={Control}
          params={definition.form_control.params}
          slots={slots}
          onChange={setSlots}
          errors={errors}
        />
        <Field
          id={noteFieldId}
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === noteFieldId)?.message}
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

function SpecimenUnconstrainedDialog({
  businessKey,
  rowVersion,
  current,
  onClose,
  onSaved,
}: {
  businessKey: string;
  rowVersion: number;
  current: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [checked, setChecked] = useState(current);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const patch = usePatchEntryCore(businessKey);
  const noteFieldId = "specimen-unconstrained-note";

  return (
    <Dialog open onClose={onClose} title="Accepts any specimen (Any)">
      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={patch.isPending}
        errors={errors}
        formError={patch.isError ? <RefusalNotice error={patch.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = noteError(note, noteFieldId);
          setErrors(found);
          if (found.length > 0) {
            return;
          }
          patch.mutate(
            {
              specimen_unconstrained: checked,
              reason: note,
              expected_row_version: rowVersion,
            },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <Checkbox
          label="This entry accepts any specimen (Any)"
          checked={checked}
          onChange={(event) => setChecked(event.target.checked)}
          hint="Turning this on while specimen codes are already recorded on this entry will be refused - clear them first."
        />
        <Field
          id={noteFieldId}
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === noteFieldId)?.message}
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
