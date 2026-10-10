import { ApiError } from "../api/unwrap.ts";
import {
  asDuplicateMatches,
  asFieldRefusal,
  asPropertyValidationError,
  asQuotaRefusal,
  refusalDetail,
  retryAfterSeconds,
} from "../api/conflicts.ts";
import type { DuplicatesBody } from "../api/conflicts.ts";
import { asTermsRequired } from "../api/terms.ts";
import { groupFieldId, slotFieldId } from "../catalogue/property-controls/index.ts";
import type { FormError } from "../components/error-summary.tsx";
import { CODE_UNAVAILABLE_MESSAGE, FIELD_IDS } from "./form-state.ts";
import { quotaMessage } from "./quota-message.ts";

/**
 * Turning a refused submission into what the form shows (FR-25, FR-26, FR-27,
 * FR-43, FR-54, NFR-45). Each refusal is told apart by its status and a
 * field in its body, never by the wording of its sentence.
 */

export type Outcome =
  | { kind: "duplicates"; body: DuplicatesBody }
  | { kind: "refused"; fieldErrors: FormError[]; message: string | null };

export const TERMS_MESSAGE =
  "You need to accept the current terms of use before you can submit. Your answers are kept. Submit again after you accept.";

const FALLBACK_MESSAGE =
  "The test could not be submitted. Check your connection and try again, or contact an administrator if the problem persists.";

export function readRefusal(
  error: unknown,
  submittedIndexes: Record<string, number[]>,
): Outcome {
  const duplicates = asDuplicateMatches(error);
  if (duplicates !== null) {
    return { kind: "duplicates", body: duplicates };
  }
  const quota = asQuotaRefusal(error);
  if (quota !== null) {
    return {
      kind: "refused",
      fieldErrors: [],
      message: quotaMessage(quota, retryAfterSeconds(error)),
    };
  }
  const field = asFieldRefusal(error);
  if (field !== null) {
    const outage =
      field.field === "snomed_code" && error instanceof ApiError && error.status === 503;
    return {
      kind: "refused",
      fieldErrors: [
        {
          fieldId: FIELD_IDS[field.field],
          message: outage ? CODE_UNAVAILABLE_MESSAGE : field.detail,
        },
      ],
      message: null,
    };
  }
  const validation = asPropertyValidationError(error);
  if (validation !== null) {
    return {
      kind: "refused",
      fieldErrors: validation.issues.map((issue) => ({
        fieldId:
          issue.ordinal === null
            ? groupFieldId(issue.property_key)
            : slotFieldId(
                issue.property_key,
                submittedIndexes[issue.property_key]?.[issue.ordinal] ?? issue.ordinal,
              ),
        message: issue.message,
      })),
      message: null,
    };
  }
  if (asTermsRequired(error) !== null) {
    return { kind: "refused", fieldErrors: [], message: TERMS_MESSAGE };
  }
  return {
    kind: "refused",
    fieldErrors: [],
    message: refusalDetail(error) ?? FALLBACK_MESSAGE,
  };
}
