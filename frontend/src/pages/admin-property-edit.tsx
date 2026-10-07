import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { useAmendProperty, useDatatypes, usePropertyDefinition } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { cardinalityLabelFor, scopeLabelFor } from "../catalogue/property-display.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { Checkbox } from "../components/checkbox.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import {
  amendValuesFrom,
  buildAmendRequest,
  type AmendValues,
} from "../registry/property-amend.ts";
import { constraintKeysOf, PROPERTY_FIELD_IDS } from "../registry/property-form.ts";
import {
  propertyLoadFailureMessage,
  propertyStaleWarning,
} from "../registry/property-load.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { PropertyRefusalNotice } from "../registry/refusal-notice.tsx";
import { releaseFocus } from "../registry/release-focus.ts";

/**
 * The amend-property screen (FR-09, FR-12, NFR-31). The key, datatype,
 * cardinality and scope are shown as text because the API refuses to change
 * them. Only the fields the editor changed are sent, with the row version the
 * screen last read.
 *
 * No permission check runs here: the API decides, and a caller without
 * `registry.manage` sees its refusal (NFR-20).
 */

type Definition = components["schemas"]["PropertyDefinitionResponse"];

const FALLBACK_REFUSAL =
  "The change could not be saved. Check the details and try again, or contact an administrator if the problem persists.";

function FixedFields({ definition }: { definition: Definition }) {
  return (
    <section aria-labelledby="property-fixed-heading">
      <h2 id="property-fixed-heading" className="m-0 mb-2 text-lg">
        Fixed once created
      </h2>
      <p className="m-0 mb-3 text-sm text-[var(--color-text-muted)]">
        These cannot be changed. Exports address the property by its key.
      </p>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2">
        <dt className="text-[var(--color-text-muted)]">Key</dt>
        <dd className="m-0 font-mono">{definition.key}</dd>
        <dt className="text-[var(--color-text-muted)]">Datatype</dt>
        <dd className="m-0 font-mono">{definition.datatype}</dd>
        <dt className="text-[var(--color-text-muted)]">Cardinality</dt>
        <dd className="m-0">{cardinalityLabelFor(definition.cardinality)}</dd>
        <dt className="text-[var(--color-text-muted)]">Scope</dt>
        <dd className="m-0">{scopeLabelFor(definition.scope)}</dd>
      </dl>
    </section>
  );
}

function AmendForm({
  definition,
  allowedConstraintKeys,
}: {
  definition: Definition;
  allowedConstraintKeys: string[] | null;
}) {
  const [initial] = useState<AmendValues>(() => amendValuesFrom(definition));
  const [values, setValues] = useState<AmendValues>(initial);
  const [errors, setErrors] = useState<FormError[]>([]);
  const amend = useAmendProperty(definition.key);
  const navigate = useNavigate();

  function set<K extends keyof AmendValues>(field: K, value: AmendValues[K]) {
    setValues((previous) => ({ ...previous, [field]: value }));
  }

  function errorFor(fieldId: string): string | undefined {
    return errors.find((error) => error.fieldId === fieldId)?.message;
  }

  const constraintsHint =
    allowedConstraintKeys === null
      ? "Optional. A JSON object. Saving replaces the whole object."
      : allowedConstraintKeys.length > 0
        ? `Optional. A JSON object using only these names: ${allowedConstraintKeys.join(", ")}. Saving replaces the whole object.`
        : "This datatype takes no constraints, so leave it empty.";

  return (
    <Card>
      <Form
        submitLabel="Save changes"
        pendingLabel="Saving"
        pending={amend.isPending}
        errors={errors}
        formError={
          amend.isError ? (
            <PropertyRefusalNotice error={amend.error} fallback={FALLBACK_REFUSAL} />
          ) : undefined
        }
        onSubmit={() => {
          const result = buildAmendRequest(values, initial, definition.row_version);
          setErrors(result.errors);
          if (result.body === null) {
            return;
          }
          return amend.mutateAsync(result.body).then(
            () => {
              releaseFocus();
              void navigate({
                to: "/admin/properties/$propertyKey",
                params: { propertyKey: definition.key },
              });
              return { ok: true };
            },
            () => ({ ok: false }),
          );
        }}
        secondaryActions={
          <Link
            to="/admin/properties/$propertyKey"
            params={{ propertyKey: definition.key }}
            className={buttonClassName("secondary")}
          >
            Cancel
          </Link>
        }
      >
        <FixedFields definition={definition} />

        <Field
          id={PROPERTY_FIELD_IDS.label}
          label="Label"
          hint="The name people see. Exports use the key, so changing the label breaks nothing."
          error={errorFor(PROPERTY_FIELD_IDS.label)}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={values.label}
              onChange={(event) => set("label", event.target.value)}
            />
          )}
        </Field>

        <Field
          id={PROPERTY_FIELD_IDS.displayOrder}
          label="Display order"
          hint="Lower numbers come first. Leave empty for 0."
          error={errorFor(PROPERTY_FIELD_IDS.displayOrder)}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              inputMode="numeric"
              value={values.displayOrder}
              onChange={(event) => set("displayOrder", event.target.value)}
            />
          )}
        </Field>

        <Checkbox
          label="Required for submission"
          checked={values.requiredForSubmission}
          onChange={(event) => set("requiredForSubmission", event.target.checked)}
        />
        <Checkbox
          label="Required for publication"
          checked={values.requiredForPublication}
          onChange={(event) => set("requiredForPublication", event.target.checked)}
        />
        <Checkbox
          label="Used as a catalogue filter"
          checked={values.filterable}
          onChange={(event) => set("filterable", event.target.checked)}
        />

        <Field
          id={PROPERTY_FIELD_IDS.constraints}
          label="Constraints"
          hint={constraintsHint}
          error={errorFor(PROPERTY_FIELD_IDS.constraints)}
        >
          {(controlProps) => (
            <textarea
              {...controlProps}
              rows={4}
              className="font-mono"
              value={values.constraintsText}
              onChange={(event) => set("constraintsText", event.target.value)}
            />
          )}
        </Field>

        <ReasonField
          id={PROPERTY_FIELD_IDS.reason}
          value={values.reason}
          onChange={(reason) => set("reason", reason)}
          error={errorFor(PROPERTY_FIELD_IDS.reason)}
        />
      </Form>
    </Card>
  );
}

export function AdminPropertyEditPage() {
  const { propertyKey } = useParams({
    from: "/authenticated/admin/properties/$propertyKey/edit",
  });
  const property = usePropertyDefinition(propertyKey);
  const datatypes = useDatatypes();
  const { message, politeness, announce } = useAnnounce();

  const staleData = property.isError && property.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(propertyStaleWarning(propertyKey));
    }
  }, [staleData, propertyKey, announce]);

  // The message, not the error, is the dependency: a refetch that fails the
  // same way yields a new error object with unchanged wording.
  const hardFailureMessage =
    property.isError && property.data === undefined
      ? propertyLoadFailureMessage(propertyKey, property.error)
      : null;
  useEffect(() => {
    if (hardFailureMessage !== null) {
      announce(hardFailureMessage);
    }
  }, [hardFailureMessage, announce]);

  const definition = property.data;
  const described = datatypes.data?.items.find(
    (item) => item.name === definition?.datatype,
  );
  const allowedConstraintKeys = described
    ? constraintKeysOf(described.constraints_schema)
    : null;

  return (
    <section aria-labelledby="property-edit-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id="property-edit-heading"
          title={definition ? `Edit ${definition.label}` : `Edit ${propertyKey}`}
          actions={
            <Link
              to="/admin/properties/$propertyKey"
              params={{ propertyKey }}
              className={buttonClassName("secondary")}
            >
              Back to the property
            </Link>
          }
        />

        {property.isPending && <p>Loading {propertyKey}…</p>}

        {hardFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p>{propertyStaleWarning(propertyKey)}</p>}

        {definition && (
          <AmendForm
            definition={definition}
            allowedConstraintKeys={allowedConstraintKeys}
          />
        )}
      </PageContainer>
    </section>
  );
}
