import { useState } from "react";

import { useBulkSavePropertyValues, usePropertyDefinitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { Select } from "../components/select.tsx";
import { ChangelogNoteField, useChangelogNote } from "./changelog-note-field.tsx";
import { RefusalNotice } from "./collision-notice.tsx";
import { propertyValidationFieldErrors } from "./property-form-errors.ts";
import {
  CONTROLS,
  RepeatableValues,
  groupFieldId,
  isEmptySlotValue,
} from "./property-controls/index.ts";
import type { PropertyValueSlot } from "./property-controls/index.ts";

type PropertyCardinality = components["schemas"]["PropertyCardinality"];
type BulkPropertyEntryTarget = components["schemas"]["BulkPropertyEntryTarget"];
type BulkSavePropertyValuesResult = components["schemas"]["BulkSavePropertyValuesResult"];

/** Matches the server's own `_MAX_BULK_ENTRIES` (ADR-0035) - a batch over
 * this is a 422 for the whole request, so the dialog refuses to submit one
 * rather than let an operator discover the limit as a server refusal. */
export const BULK_RECLASSIFY_MAX_ENTRIES = 100;

const PROPERTY_FIELD_ID = "bulk-reclassify-property";

/** Same filter/dedupe logic `properties-panel.tsx`'s own `PropertyEditDialog`
 * uses for its single-entry save (see that file's own doc comment): a
 * server issue's `ordinal` indexes the slots actually sent, not every
 * rendered slot. */
function nonEmptySlotIndexes(slots: PropertyValueSlot[]): number[] {
  return slots.reduce<number[]>((indexes, slot, index) => {
    if (!isEmptySlotValue(slot.value)) {
      indexes.push(index);
    }
    return indexes;
  }, []);
}

function overCapMessage(count: number): string {
  return (
    `Select ${BULK_RECLASSIFY_MAX_ENTRIES} or fewer entries to reclassify them ` +
    `together - ${count} are currently selected.`
  );
}

/**
 * Reclassify one registry property to one value set across every selected
 * entry, in a single audited batch (issue #63; FR-38, FR-39, FR-44).
 *
 * Reuses `properties-panel.tsx`'s own `PropertyEditDialog` composition
 * almost unchanged - `RepeatableValues` + `CONTROLS[...]` + `useChangelogNote`
 * + `Form` - with two differences forced by there being many targets, not
 * one: a property picker (there is no single row's own Edit button to have
 * already named it), and no pre-filled values (every targeted entry
 * *replaces* its whole set, so there is no single "current value" to show -
 * see the warning rendered below).
 *
 * **No client-side permission gating** (NFR-20): the action is always
 * rendered; a caller without `catalogue.edit_published` (or who holds it but
 * has not completed step-up) is refused by the server and sees it through
 * `RefusalNotice`, the same as every other write in this app.
 *
 * **Never retries a conflict.** This route never throws a version conflict
 * (ADR-0035) - a stale `expected_row_version` is a per-entry outcome in the
 * *success* body, so there is nothing for this dialog's own error handling
 * to react to; the caller's `onComplete` is what shows those to the
 * operator, via `BulkOutcomeSummary`.
 */
export function BulkReclassifyDialog({
  entries,
  onClose,
  onComplete,
}: {
  entries: BulkPropertyEntryTarget[];
  onClose: () => void;
  onComplete: (result: BulkSavePropertyValuesResult, propertyLabel: string) => void;
}) {
  const definitions = usePropertyDefinitions();
  const [propertyKey, setPropertyKey] = useState("");
  const [slots, setSlots] = useState<PropertyValueSlot[]>([]);
  const noteFieldId = "bulk-reclassify-note";
  const changelogNote = useChangelogNote(noteFieldId);
  const save = useBulkSavePropertyValues(propertyKey);

  // Only a property currently open for new entry can be a reclassify
  // target - matching `properties-panel.tsx`'s own `buildRows` rule that a
  // deprecated property takes no new value, generated fresh from the
  // registry rather than naming any property key here (ADR-0013, FR-77).
  // Sorted by `display_order`, matching `buildRows`' own generation order
  // (FR-09), so the picker lists properties the same way the single-entry
  // panel does.
  const activeDefinitions = (definitions.data?.items ?? [])
    .filter((definition) => definition.status === "active")
    .sort((a, b) => a.display_order - b.display_order);
  const selectedDefinition =
    activeDefinitions.find((definition) => definition.key === propertyKey) ?? null;
  const Control = selectedDefinition
    ? CONTROLS[selectedDefinition.form_control.control]
    : null;

  const overCap = entries.length > BULK_RECLASSIFY_MAX_ENTRIES;
  const submittedIndexes = nonEmptySlotIndexes(slots);
  // `selectedDefinition?.key`, not the raw `propertyKey` state - both name
  // the same property once a valid one is selected, but this reads it back
  // off the server-sourced `activeDefinitions` array rather than off the
  // `<select>`'s own DOM value, the same reasoning the `onChange` handler
  // below re-validates against that array before storing anything.
  const validationErrors: FormError[] = propertyValidationFieldErrors(
    selectedDefinition?.key ?? "",
    save.error,
    submittedIndexes,
  );
  // A field-level 422 is rendered per-value above; a refusal with no field
  // attribution (no permission, a deprecated property, FR-89's whole-batch
  // abort with `ordinal: null`) falls back to the generic slot instead.
  const genericRefusal = save.isError && validationErrors.length === 0;

  const noValuesEntered = selectedDefinition !== null && submittedIndexes.length === 0;

  // `Form` takes one `submitBlocked`/`blockedReason` pair, so the four gates
  // this dialog has - the cap, no property chosen yet, no value entered, and
  // the changelog note - are composed here in priority order, each unlinked
  // except the three with an obvious field to send focus to.
  //
  // The no-values gate exists because the server places no floor on
  // `values.length` (a bulk write is a whole-set replace, and an empty set is
  // a legitimate way to *clear* a property - see `catalogue_properties.py`):
  // without it, choosing a property and submitting with every auto-rendered
  // slot left blank clears that property across every selected entry, which
  // reads nothing like what "Every value set here replaces..." above warns
  // about.
  const blockedReason = overCap
    ? overCapMessage(entries.length)
    : selectedDefinition === null
      ? "Choose a property to reclassify."
      : noValuesEntered
        ? "Add at least one value before reclassifying."
        : changelogNote.blockedReason;
  const blockedFieldId = overCap
    ? undefined
    : selectedDefinition === null
      ? PROPERTY_FIELD_ID
      : noValuesEntered
        ? groupFieldId(selectedDefinition.key)
        : changelogNote.fieldId;

  return (
    <Dialog open onClose={onClose} title="Reclassify selected entries">
      <Form
        submitLabel="Reclassify"
        pendingLabel="Reclassifying"
        pending={save.isPending}
        errors={validationErrors}
        formError={genericRefusal ? <RefusalNotice error={save.error} /> : undefined}
        submitBlocked={
          overCap ||
          selectedDefinition === null ||
          noValuesEntered ||
          changelogNote.blocked
        }
        blockedReason={blockedReason}
        blockedFieldId={blockedFieldId}
        onSubmitBlocked={changelogNote.markSubmitAttempted}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          if (selectedDefinition === null) {
            return;
          }
          const submitted = submittedIndexes.map((index) => ({
            value: slots[index].value,
            justification: slots[index].justification,
          }));
          save.mutate(
            { values: submitted, reason: changelogNote.note, entries },
            {
              onSuccess: (result) => onComplete(result, selectedDefinition.label),
            },
          );
        }}
      >
        <p>
          Every value set here replaces whatever this property currently holds on each of
          the {entries.length} selected entries, including a compound value already
          recorded - nothing is merged with what is there now.
        </p>

        <Select
          label="Property"
          id={PROPERTY_FIELD_ID}
          value={propertyKey}
          placeholder="Choose a property"
          options={activeDefinitions.map((definition) => ({
            value: definition.key,
            label: definition.label,
          }))}
          onChange={(event) => {
            // Re-validated against the registry's own definitions, rather
            // than stored as the raw `<select>` value: every downstream use
            // of this key (the mutation path segment, the error-summary
            // field ids `RepeatableValues` derives from it) should trace
            // back to server-sourced data, not to an unvalidated DOM read.
            const next =
              activeDefinitions.find(
                (definition) => definition.key === event.target.value,
              )?.key ?? "";
            setPropertyKey(next);
            setSlots([]);
            // The note is property-independent (it describes the batch, not
            // one property's values), so switching properties clears the
            // now-stale `slots` but must leave it alone - `reset()` would
            // also discard any note text already typed (review finding on
            // PR #290).
            if (save.isError) {
              save.reset();
            }
          }}
        />

        {selectedDefinition && Control && (
          <RepeatableValues
            propertyKey={selectedDefinition.key}
            label={selectedDefinition.label}
            cardinality={selectedDefinition.cardinality as PropertyCardinality}
            control={Control}
            params={selectedDefinition.form_control.params}
            slots={slots}
            onChange={(next) => {
              setSlots(next);
              if (save.isError) {
                save.reset();
              }
            }}
            errors={validationErrors}
          />
        )}

        <ChangelogNoteField id={noteFieldId} changelogNote={changelogNote} />
      </Form>
    </Dialog>
  );
}
