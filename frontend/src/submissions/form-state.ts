import type { SubmissionField } from "../api/conflicts.ts";
import type { components } from "../api/schema.ts";
import { SUBMISSION_LIMITS } from "../catalogue/limits.ts";
import {
  isEmptySlotValue,
  newSlotId,
  slotFieldId,
} from "../catalogue/property-controls/index.ts";
import type { PropertyValueSlot } from "../catalogue/property-controls/index.ts";
import type { CodeSelection } from "../catalogue/use-code-selection.ts";
import type { FormError } from "../components/error-summary.tsx";

/**
 * What the new-test form holds, and how it becomes a request (FR-23, FR-24).
 *
 * Every check here is about wording and about a field the server needs before
 * it can answer at all. The server stays the authority: it cleans the terms,
 * checks the code and fetches the reference link (ADR-0030).
 */

type CreateSubmissionBody = components["schemas"]["CreateSubmissionRequest"];
type DuplicateCheckBody = components["schemas"]["DuplicateCheckRequest"];
type PropertyDefinition = components["schemas"]["PropertyDefinitionResponse"];

/** The id of each control a server refusal can name. */
export const FIELD_IDS: Record<SubmissionField, string> = {
  preferred_term: "submission-preferred-term",
  synonyms: "submission-other-names",
  snomed_code: "submission-code-search",
  reference_url: "submission-reference-url",
  notes: "submission-notes",
  organisation: "submission-organisation",
};

export interface NameRow {
  id: string;
  term: string;
}

export interface SubmissionValues {
  preferredTerm: string;
  names: NameRow[];
  /** The code the user picked. Sent only once the terminology server has named it. */
  code: string | null;
  slots: Record<string, PropertyValueSlot[]>;
  referenceUrl: string;
  notes: string;
  /** `null` until the user edits it, so the profile's value shows and is not copied. */
  organisation: string | null;
}

export function blankName(): NameRow {
  return { id: newSlotId(), term: "" };
}

export function initialValues(): SubmissionValues {
  return {
    preferredTerm: "",
    names: [blankName()],
    code: null,
    slots: {},
    referenceUrl: "",
    notes: "",
    organisation: null,
  };
}

/** The properties the form shows, in the registry's display order. */
export function submissionRows(definitions: PropertyDefinition[]): PropertyDefinition[] {
  return [...definitions].sort((a, b) => a.display_order - b.display_order);
}

export function filledNames(names: NameRow[]): string[] {
  return names.map((row) => row.term.trim()).filter((term) => term.length > 0);
}

/** The render index of each slot that holds a value, which is what the server's `ordinal` counts. */
export function filledSlotIndexes(slots: PropertyValueSlot[]): number[] {
  return slots.reduce<number[]>((indexes, slot, index) => {
    if (!isEmptySlotValue(slot.value)) {
      indexes.push(index);
    }
    return indexes;
  }, []);
}

export function submittedIndexesByKey(
  slots: Record<string, PropertyValueSlot[]>,
): Record<string, number[]> {
  return Object.fromEntries(
    Object.entries(slots).map(([key, list]) => [key, filledSlotIndexes(list)]),
  );
}

export const CODE_UNAVAILABLE_MESSAGE =
  "The terminology server could not be reached, so the code could not be checked. Clear the code to send the form without one, or try again shortly.";

/** Why a picked code cannot be sent, and what the user can do about it. */
export function unresolvedCodeMessage(
  selection: Extract<CodeSelection, { status: "unresolved" }>,
): string {
  return selection.unavailable
    ? CODE_UNAVAILABLE_MESSAGE
    : `${selection.message} Clear the code to send the form without one.`;
}

function overLimit(count: number, limit: number): boolean {
  return count > limit;
}

/**
 * The form's own mistakes, found before anything is sent. A code that is still
 * being checked, or that the server could not name, holds the form back: the
 * user clears it to send the form without one.
 */
export function validate(
  values: SubmissionValues,
  definitions: PropertyDefinition[],
  selection: CodeSelection,
): FormError[] {
  const found: FormError[] = [];
  const { termLength, synonyms, notesLength, organisationLength } = SUBMISSION_LIMITS;

  if (values.preferredTerm.trim() === "") {
    found.push({ fieldId: FIELD_IDS.preferred_term, message: "Enter the test name." });
  } else if (overLimit(values.preferredTerm.length, termLength)) {
    found.push({
      fieldId: FIELD_IDS.preferred_term,
      message: `The test name is over ${termLength} characters. Shorten it.`,
    });
  }

  const names = filledNames(values.names);
  if (overLimit(names.length, synonyms)) {
    found.push({
      fieldId: FIELD_IDS.synonyms,
      message: `You can give at most ${synonyms} other names. Remove ${names.length - synonyms}.`,
    });
  } else if (names.some((name) => overLimit(name.length, termLength))) {
    found.push({
      fieldId: FIELD_IDS.synonyms,
      message: `An other name is over ${termLength} characters. Shorten it.`,
    });
  }

  if (selection.status === "checking") {
    found.push({
      fieldId: FIELD_IDS.snomed_code,
      message: "Wait for the code to finish checking, or clear it.",
    });
  } else if (selection.status === "unresolved") {
    found.push({
      fieldId: FIELD_IDS.snomed_code,
      message: unresolvedCodeMessage(selection),
    });
  }

  for (const definition of definitions) {
    const list = values.slots[definition.key] ?? [];
    const filled = filledSlotIndexes(list).length;
    if (definition.required_for_submission && filled === 0) {
      found.push({
        fieldId: slotFieldId(definition.key, 0),
        message: `Enter a value for ${definition.label}.`,
      });
    } else if (overLimit(filled, SUBMISSION_LIMITS.valuesPerProperty)) {
      found.push({
        fieldId: slotFieldId(definition.key, 0),
        message: `${definition.label} can have at most ${SUBMISSION_LIMITS.valuesPerProperty} values.`,
      });
    }
  }

  if (values.referenceUrl.trim() === "") {
    found.push({
      fieldId: FIELD_IDS.reference_url,
      message: "Enter a link to a web page that supports this test.",
    });
  } else if (
    overLimit(values.referenceUrl.trim().length, SUBMISSION_LIMITS.referenceUrlLength)
  ) {
    found.push({
      fieldId: FIELD_IDS.reference_url,
      message: `The link is over ${SUBMISSION_LIMITS.referenceUrlLength} characters. Use a shorter address.`,
    });
  }

  if (overLimit(values.notes.length, notesLength)) {
    found.push({
      fieldId: FIELD_IDS.notes,
      message: `The notes are over ${notesLength} characters. Shorten them.`,
    });
  }
  if (
    values.organisation !== null &&
    overLimit(values.organisation.length, organisationLength)
  ) {
    found.push({
      fieldId: FIELD_IDS.organisation,
      message: `The organisation is over ${organisationLength} characters. Shorten it.`,
    });
  }
  return found;
}

/** The code to send, which is always the string the terminology server returned (FR-06, FR-26). */
function codeToSend(selection: CodeSelection): string | undefined {
  return selection.status === "ready" ? selection.concept.code : undefined;
}

export function duplicateCheckBody(
  values: SubmissionValues,
  selection: CodeSelection,
): DuplicateCheckBody {
  const code = codeToSend(selection);
  return {
    preferred_term: values.preferredTerm,
    synonyms: filledNames(values.names),
    ...(code === undefined ? {} : { snomed_code: code }),
  };
}

export function createBody(
  values: SubmissionValues,
  definitions: PropertyDefinition[],
  selection: CodeSelection,
  confirmNotDuplicate: boolean,
): CreateSubmissionBody {
  const propertyValues: NonNullable<CreateSubmissionBody["property_values"]> = {};
  for (const definition of definitions) {
    const list = values.slots[definition.key] ?? [];
    const filled = filledSlotIndexes(list).map((index) => list[index]);
    if (filled.length > 0) {
      propertyValues[definition.key] = filled.map((slot) => ({
        value: slot.value,
        justification:
          slot.justification === null || slot.justification === ""
            ? null
            : slot.justification,
      }));
    }
  }
  return {
    ...duplicateCheckBody(values, selection),
    reference_url: values.referenceUrl.trim(),
    property_values: propertyValues,
    ...(values.notes.trim() === "" ? {} : { notes: values.notes }),
    ...(values.organisation === null ? {} : { organisation: values.organisation }),
    confirm_not_duplicate: confirmNotDuplicate,
  };
}
