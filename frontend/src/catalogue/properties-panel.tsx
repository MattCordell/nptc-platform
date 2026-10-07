import { useState } from "react";

import { asPropertyValidationError, refusalDetail } from "../api/conflicts.ts";
import { usePropertyDefinitions, useSavePropertyValues } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import { formatPropertyValue } from "./format-property-value.ts";
import { propertyValidationFieldErrors } from "./property-form-errors.ts";
import {
  CONTROLS,
  RepeatableValues,
  isEmptySlotValue,
  newSlotId,
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
 * **`specimen` is an ordinary coded property here.** "Accepts any specimen"
 * is the root code `123038009` held alone, edited through the same dialog as
 * every other property (FR-89, ADR-0044). The write API refuses the root
 * beside another specimen, and that refusal shows as a field error. The
 * picker shows the server's display for the root, "Specimen", where a loaded
 * workbook stores "Any", so the specimen dialog says which value to pick.
 */

const SPECIMEN_KEY = "specimen";

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

function formatValues(values: PropertyValue[]): string {
  if (values.length === 0) {
    return "No value recorded.";
  }
  return values.map((value) => formatPropertyValue(value.value)).join(", ");
}

/** The indexes into `slots` that `isEmptySlotValue` keeps for a save - see
 * `PropertyEditDialog`'s own note on why the same list drives both the
 * submitted body and the server-issue-to-slot mapping. */
function nonEmptySlotIndexes(slots: PropertyValueSlot[]): number[] {
  return slots.reduce<number[]>((indexes, slot, index) => {
    if (!isEmptySlotValue(slot.value)) {
      indexes.push(index);
    }
    return indexes;
  }, []);
}

export function PropertiesPanel({ entry }: { entry: EntryDetail }) {
  const businessKey = entry.business_key;
  const definitions = usePropertyDefinitions();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const { message, politeness, announce } = useAnnounce();

  const rows = definitions.data
    ? buildRows(definitions.data.items, entry.properties)
    : [];
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
            {
              key: "values",
              header: "Values",
              render: (row) => formatValues(row.values),
            },
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
    </section>
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
    values.map((value) => ({
      id: newSlotId(),
      value: value.value,
      justification: value.justification,
    })),
  );
  const noteFieldId = `${definition.key}-note`;
  const changelogNote = useChangelogNote(noteFieldId);
  const save = useSavePropertyValues(businessKey, definition.key);
  const Control = CONTROLS[definition.form_control.control];

  // `save.mutate` below sends only the slots `isEmptySlotValue` keeps, so a
  // `PropertyIssueItem.ordinal` from the server indexes *that* filtered
  // array, not `slots` as rendered. `submittedIndexes[issue.ordinal]` is the
  // one place both directions agree: the render index a field-level error
  // must attach to for `slotFieldId` to land on the slot that actually
  // produced it, rather than whichever slot happens to sit at the server's
  // raw ordinal (wrong the moment an earlier slot is left blank).
  const submittedIndexes = nonEmptySlotIndexes(slots);
  const validation = asPropertyValidationError(save.error);
  const errors: FormError[] = propertyValidationFieldErrors(
    definition.key,
    save.error,
    submittedIndexes,
  );

  return (
    <Dialog open onClose={onClose} title={`Edit ${definition.label}`}>
      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={save.isPending}
        errors={errors}
        // The field-level 422 (`validation`) is already rendered per-field via
        // `errors` above; the generic slot only carries a refusal with no
        // field attribution (a write against a now-deprecated property,
        // FR-38's conflict).
        formError={
          save.isError && validation === null ? (
            <RefusalNotice error={save.error} />
          ) : undefined
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
          const submitted = submittedIndexes.map((index) => ({
            value: slots[index].value,
            justification: slots[index].justification,
          }));
          save.mutate(
            {
              values: submitted,
              reason: changelogNote.note,
              expected_row_version: rowVersion,
            },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        {definition.key === SPECIMEN_KEY ? (
          <p className="text-sm text-[var(--color-text-muted)]">
            To record that a test accepts any specimen, choose Specimen (123038009) on its
            own. A workbook import stores the same value as Any.
          </p>
        ) : null}
        <RepeatableValues
          propertyKey={definition.key}
          label={definition.label}
          cardinality={definition.cardinality as PropertyCardinality}
          control={Control}
          params={definition.form_control.params}
          slots={slots}
          onChange={(next) => {
            setSlots(next);
            // A field-level 422 is rendered against `slots` as they stood at
            // submit time; editing after a refusal must not leave it stuck
            // against a value already changed, and - since `submittedIndexes`
            // above is recomputed from current `slots` - must not leave a
            // stale error's `ordinal` pointing at a slot a Remove/Add just
            // shifted out from under it.
            if (save.isError) {
              save.reset();
            }
          }}
          errors={errors}
        />
        <ChangelogNoteField id={noteFieldId} changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}
