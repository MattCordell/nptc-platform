import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { FIELD_IDS } from "./form-state.ts";
import type { SubmissionValues } from "./form-state.ts";

/** The example in the reference-link hint: a published method suits a new test, not a new name. */
const REFERENCE_EXAMPLE = {
  test: "a guideline or a published method",
  change: "a guideline or a supplier's test directory",
} as const;

/**
 * The reference link, notes and organisation that a new test and an amendment
 * both carry (FR-23, FR-35). A new test needs a reference link and an
 * amendment does not, which is the only difference in what they ask.
 */
export function SupportFields({
  values,
  errorFor,
  onEdit,
  defaultOrganisation,
  referenceRequired,
  noun,
}: {
  values: Pick<SubmissionValues, "referenceUrl" | "notes" | "organisation">;
  errorFor: (fieldId: string) => string | undefined;
  onEdit: (patch: Partial<SubmissionValues>, fieldId: string) => void;
  defaultOrganisation: string;
  referenceRequired: boolean;
  noun: "test" | "change";
}) {
  return (
    <>
      <Field
        id={FIELD_IDS.reference_url}
        label={referenceRequired ? "Reference link (required)" : "Reference link"}
        hint={`${referenceRequired ? "" : "Optional. "}A web page that supports this ${noun}, such as ${REFERENCE_EXAMPLE[noun]}. The platform checks that the page answers.`}
        error={errorFor(FIELD_IDS.reference_url)}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            className={INPUT_CLASSES}
            type="text"
            inputMode="url"
            autoComplete="off"
            value={values.referenceUrl}
            onChange={(event) =>
              onEdit({ referenceUrl: event.target.value }, FIELD_IDS.reference_url)
            }
          />
        )}
      </Field>

      <Field
        id={FIELD_IDS.notes}
        label="Notes"
        hint="Anything a reviewer should know."
        error={errorFor(FIELD_IDS.notes)}
      >
        {(controlProps) => (
          <textarea
            {...controlProps}
            className={INPUT_CLASSES}
            rows={4}
            value={values.notes}
            onChange={(event) => onEdit({ notes: event.target.value }, FIELD_IDS.notes)}
          />
        )}
      </Field>

      <Field
        id={FIELD_IDS.organisation}
        label="Organisation"
        hint={`Filled in from your profile. Change it if this ${noun} comes from a different organisation.`}
        error={errorFor(FIELD_IDS.organisation)}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            className={INPUT_CLASSES}
            type="text"
            value={values.organisation ?? defaultOrganisation}
            onChange={(event) =>
              onEdit({ organisation: event.target.value }, FIELD_IDS.organisation)
            }
          />
        )}
      </Field>
    </>
  );
}
