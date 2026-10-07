import { useState } from "react";

import { useDeprecateProperty } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { PROPERTY_FIELD_IDS, reasonProblem } from "./property-form.ts";
import { ReasonField } from "./reason-field.tsx";
import { PropertyRefusalNotice } from "./refusal-notice.tsx";

/**
 * Confirmation for retiring a property (FR-11). Deprecation is one-way: the
 * API refuses to reactivate, so the dialog says so before it asks for a reason.
 *
 * No permission check runs here: the API decides, and a caller without
 * `registry.manage`, or one deprecating a system property, sees the refusal in
 * the dialog (NFR-20).
 */

type Definition = components["schemas"]["PropertyDefinitionResponse"];

const FALLBACK_REFUSAL =
  "The property could not be deprecated. Try again, or contact an administrator if the problem persists.";

export function DeprecatePropertyDialog({
  definition,
  onClose,
  onDeprecated,
}: {
  definition: Definition;
  onClose: () => void;
  onDeprecated: () => void;
}) {
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const deprecate = useDeprecateProperty(definition.key);

  return (
    <Dialog open onClose={onClose} title={`Deprecate ${definition.label}`}>
      <Form
        submitLabel="Deprecate property"
        pendingLabel="Deprecating"
        pending={deprecate.isPending}
        errors={errors}
        formError={
          deprecate.isError ? (
            <PropertyRefusalNotice error={deprecate.error} fallback={FALLBACK_REFUSAL} />
          ) : undefined
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const missing = reasonProblem(reason, "deprecating this property");
          setErrors(
            missing === null
              ? []
              : [{ fieldId: PROPERTY_FIELD_IDS.reason, message: missing }],
          );
          if (missing !== null) {
            // An earlier server refusal no longer describes this attempt.
            deprecate.reset();
            return;
          }
          return deprecate
            .mutateAsync({
              expected_row_version: definition.row_version,
              reason: reason.trim(),
            })
            .then(
              () => {
                onDeprecated();
                return { ok: true };
              },
              () => ({ ok: false }),
            );
        }}
      >
        <p>
          Deprecating {definition.label} cannot be undone. Entries keep the values already
          recorded against it, but it no longer appears on data-entry forms. To use it
          again, create a new property.
        </p>
        <ReasonField
          id={PROPERTY_FIELD_IDS.reason}
          value={reason}
          onChange={setReason}
          error={
            errors.find((error) => error.fieldId === PROPERTY_FIELD_IDS.reason)?.message
          }
        />
      </Form>
    </Dialog>
  );
}
