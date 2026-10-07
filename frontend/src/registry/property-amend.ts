import type { components } from "../api/schema.ts";
import type { FormError } from "../components/error-summary.tsx";
import {
  DISPLAY_ORDER_PROBLEM,
  parseConstraints,
  parseDisplayOrder,
  PROPERTY_FIELD_IDS,
  reasonProblem,
} from "./property-form.ts";

/**
 * The amend form's checks and request body (FR-09, FR-12). The API accepts only
 * the fields below, refuses an explicit null, and replaces `constraints` as a
 * whole, so the body carries just the fields the editor changed.
 *
 * "Changed" means different from what the form last showed. When the property
 * reloads after a stale-version refusal, `rebaseOnto` gives every untouched
 * field the new value and keeps the editor's own text, so the screen matches
 * the server and an untouched field never overwrites someone else's change.
 */

type Definition = components["schemas"]["PropertyDefinitionResponse"];
type AmendBody = components["schemas"]["AmendPropertyDefinitionRequest"];

export type AmendValues = {
  label: string;
  displayOrder: string;
  requiredForSubmission: boolean;
  requiredForPublication: boolean;
  filterable: boolean;
  constraintsText: string;
  reason: string;
};

export function amendValuesFrom(definition: Definition): AmendValues {
  return {
    label: definition.label,
    displayOrder: String(definition.display_order),
    requiredForSubmission: definition.required_for_submission,
    requiredForPublication: definition.required_for_publication,
    filterable: definition.filterable,
    constraintsText:
      Object.keys(definition.constraints).length === 0
        ? ""
        : JSON.stringify(definition.constraints, null, 2),
    reason: "",
  };
}

function keepEdit<K extends keyof AmendValues>(
  field: K,
  values: AmendValues,
  initial: AmendValues,
  latest: AmendValues,
): AmendValues[K] {
  return values[field] === initial[field] ? latest[field] : values[field];
}

/**
 * Moves the form onto a freshly loaded property. A field the editor did not
 * touch takes the new value. A field they did touch keeps their text, and is
 * now compared with the new value, so typing the old value back to undo
 * someone else's change counts as a change.
 */
export function rebaseOnto(
  values: AmendValues,
  initial: AmendValues,
  latest: AmendValues,
): { values: AmendValues; initial: AmendValues } {
  return {
    values: {
      label: keepEdit("label", values, initial, latest),
      displayOrder: keepEdit("displayOrder", values, initial, latest),
      requiredForSubmission: keepEdit("requiredForSubmission", values, initial, latest),
      requiredForPublication: keepEdit("requiredForPublication", values, initial, latest),
      filterable: keepEdit("filterable", values, initial, latest),
      constraintsText: keepEdit("constraintsText", values, initial, latest),
      reason: values.reason,
    },
    initial: latest,
  };
}

export type AmendRequestResult = { errors: FormError[]; body: AmendBody | null };

function problem(fieldId: string, message: string): FormError {
  return { fieldId, message };
}

export function buildAmendRequest(
  values: AmendValues,
  initial: AmendValues,
  expectedRowVersion: number,
): AmendRequestResult {
  const errors: FormError[] = [];
  const changes: Partial<AmendBody> = {};

  const label = values.label.trim();
  if (label.length === 0) {
    errors.push(problem(PROPERTY_FIELD_IDS.label, "Enter a label."));
  } else if (label !== initial.label.trim()) {
    changes.label = label;
  }

  const displayOrder = parseDisplayOrder(values.displayOrder);
  if (displayOrder === null) {
    errors.push(problem(PROPERTY_FIELD_IDS.displayOrder, DISPLAY_ORDER_PROBLEM));
  } else if (displayOrder !== parseDisplayOrder(initial.displayOrder)) {
    changes.display_order = displayOrder;
  }

  const constraints = parseConstraints(values.constraintsText);
  if (!constraints.ok) {
    errors.push(problem(PROPERTY_FIELD_IDS.constraints, constraints.message));
  } else {
    const before = parseConstraints(initial.constraintsText);
    if (
      !before.ok ||
      JSON.stringify(constraints.value) !== JSON.stringify(before.value)
    ) {
      changes.constraints = constraints.value;
    }
  }

  if (values.requiredForSubmission !== initial.requiredForSubmission) {
    changes.required_for_submission = values.requiredForSubmission;
  }
  if (values.requiredForPublication !== initial.requiredForPublication) {
    changes.required_for_publication = values.requiredForPublication;
  }
  if (values.filterable !== initial.filterable) {
    changes.filterable = values.filterable;
  }

  const missingReason = reasonProblem(values.reason, "this change");
  if (missingReason !== null) {
    errors.push(problem(PROPERTY_FIELD_IDS.reason, missingReason));
  }
  if (errors.length === 0 && Object.keys(changes).length === 0) {
    errors.push(
      problem(PROPERTY_FIELD_IDS.label, "Change at least one field before saving."),
    );
  }

  if (errors.length > 0) {
    return { errors, body: null };
  }
  return {
    errors,
    body: {
      ...changes,
      expected_row_version: expectedRowVersion,
      reason: values.reason.trim(),
    },
  };
}
