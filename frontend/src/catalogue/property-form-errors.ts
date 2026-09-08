import { asPropertyValidationError } from "../api/conflicts.ts";
import type { FormError } from "../components/error-summary.tsx";
import { groupFieldId, slotFieldId } from "./property-controls/index.ts";

/**
 * Maps a `PropertyValidationResponse`'s `issues[]` onto the field ids a
 * property edit dialog's slots use (issue #63's extraction from
 * `properties-panel.tsx`'s own `PropertyEditDialog`, so the bulk reclassify
 * dialog does not re-derive it a second time).
 *
 * `submittedIndexes` is the render index of each slot actually sent on the
 * wire (`nonEmptySlotIndexes`'s own result) - a server issue's `ordinal`
 * indexes *that* filtered array, not the dialog's full `slots`, so
 * `submittedIndexes[issue.ordinal]` is the one place both directions agree
 * on which rendered slot produced a given issue.
 *
 * Returns `[]` for any error that is not a `PropertyValidationResponse` -
 * the caller's own `formError` slot is where a refusal with no per-field
 * attribution belongs instead.
 */
export function propertyValidationFieldErrors(
  propertyKey: string,
  error: unknown,
  submittedIndexes: number[],
): FormError[] {
  const validation = asPropertyValidationError(error);
  return (
    validation?.issues.map((issue) => ({
      fieldId:
        issue.ordinal === null
          ? groupFieldId(propertyKey)
          : slotFieldId(propertyKey, submittedIndexes[issue.ordinal] ?? issue.ordinal),
      message: issue.message,
    })) ?? []
  );
}
