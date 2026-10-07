import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { useCreateProperty, useDatatypes } from "../api/queries.ts";
import {
  BINDING_STRENGTH_OPTIONS,
  BINDING_TARGET_OPTIONS,
  CARDINALITY_OPTIONS,
  SCOPE_OPTIONS,
} from "../catalogue/property-display.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { Checkbox } from "../components/checkbox.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Select } from "../components/select.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import {
  buildCreateRequest,
  constraintKeysOf,
  PROPERTY_FIELD_IDS,
  type CreateValues,
  EMPTY_CREATE_VALUES,
  LOCAL_CODE_SYSTEM_TARGET,
  VALUE_SET_TARGET,
} from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { PropertyRefusalNotice } from "../registry/refusal-notice.tsx";
import { releaseFocus } from "../registry/release-focus.ts";

/**
 * The create-property screen (FR-09, FR-12, NFR-31). The datatype options, and
 * whether the chosen one takes a terminology binding, come from the API, so the
 * form never names a datatype (FR-77, ADR-0013).
 *
 * A refusal is shown in the form's error slot with the server's own sentence.
 * No permission check runs here: the API decides, and a caller without
 * `registry.manage` sees its refusal (NFR-20).
 */

const FALLBACK_REFUSAL =
  "The property could not be created. Check the details and try again, or contact an administrator if the problem persists.";

const DATATYPES_FAILURE =
  "The datatypes could not be loaded, so a property cannot be created yet. Reload the page to try again.";

export function AdminPropertyCreatePage() {
  const datatypes = useDatatypes();
  const create = useCreateProperty();
  const navigate = useNavigate();
  const { message, politeness, announce } = useAnnounce();
  const [values, setValues] = useState<CreateValues>(EMPTY_CREATE_VALUES);
  const [errors, setErrors] = useState<FormError[]>([]);

  useEffect(() => {
    if (datatypes.isError) {
      announce(DATATYPES_FAILURE);
    }
  }, [datatypes.isError, announce]);

  function set<K extends keyof CreateValues>(field: K, value: CreateValues[K]) {
    setValues((previous) => ({ ...previous, [field]: value }));
  }

  function errorFor(fieldId: string): string | undefined {
    return errors.find((error) => error.fieldId === fieldId)?.message;
  }

  const items = datatypes.data?.items ?? [];
  const selected = items.find((item) => item.name === values.datatype);
  const allowedConstraintKeys = selected
    ? constraintKeysOf(selected.constraints_schema)
    : [];
  const bindingTarget = values.bindingTarget;

  return (
    <section aria-labelledby="property-create-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id="property-create-heading"
          title="New property"
          actions={
            <Link to="/admin/properties" className={buttonClassName("secondary")}>
              Back to the property registry
            </Link>
          }
        />

        {datatypes.isPending && <p>Loading the datatypes…</p>}

        {datatypes.isError && (
          <p className="m-0 text-[var(--color-danger)]">{DATATYPES_FAILURE}</p>
        )}

        {datatypes.isSuccess && (
          <Card>
            <Form
              submitLabel="Create property"
              pendingLabel="Creating"
              pending={create.isPending}
              errors={errors}
              formError={
                create.isError ? (
                  <PropertyRefusalNotice
                    error={create.error}
                    fallback={FALLBACK_REFUSAL}
                  />
                ) : undefined
              }
              onSubmit={() => {
                const result = buildCreateRequest(values, selected);
                setErrors(result.errors);
                if (result.body === null) {
                  return;
                }
                return create.mutateAsync(result.body).then(
                  (created) => {
                    releaseFocus();
                    void navigate({
                      to: "/admin/properties/$propertyKey",
                      params: { propertyKey: created.key },
                    });
                    return { ok: true };
                  },
                  () => ({ ok: false }),
                );
              }}
              secondaryActions={
                <Link to="/admin/properties" className={buttonClassName("secondary")}>
                  Cancel
                </Link>
              }
            >
              <Field
                id={PROPERTY_FIELD_IDS.key}
                label="Key"
                hint="Lowercase letters, digits and underscores, starting with a letter. It identifies the property in exports and cannot be changed later."
                error={errorFor(PROPERTY_FIELD_IDS.key)}
              >
                {(controlProps) => (
                  <input
                    {...controlProps}
                    type="text"
                    className="font-mono"
                    value={values.key}
                    onChange={(event) => set("key", event.target.value)}
                  />
                )}
              </Field>

              <Field
                id={PROPERTY_FIELD_IDS.label}
                label="Label"
                hint="The name people see. You can change it later."
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

              <Select
                id={PROPERTY_FIELD_IDS.datatype}
                label="Datatype"
                hint="What kind of value the property holds. You cannot change it later."
                placeholder="Choose a datatype"
                options={items.map((item) => ({ value: item.name, label: item.name }))}
                value={values.datatype}
                onChange={(event) => set("datatype", event.target.value)}
                error={errorFor(PROPERTY_FIELD_IDS.datatype)}
              />

              <Select
                id={PROPERTY_FIELD_IDS.cardinality}
                label="Cardinality"
                hint="How many values an entry can hold. You cannot change it later."
                placeholder="Choose a cardinality"
                options={CARDINALITY_OPTIONS}
                value={values.cardinality}
                onChange={(event) => set("cardinality", event.target.value)}
                error={errorFor(PROPERTY_FIELD_IDS.cardinality)}
              />

              <Select
                id={PROPERTY_FIELD_IDS.scope}
                label="Scope"
                hint="Whether the property appears when proposing a test, when maintaining one, or both. You cannot change it later."
                placeholder="Choose a scope"
                options={SCOPE_OPTIONS}
                value={values.scope}
                onChange={(event) => set("scope", event.target.value)}
                error={errorFor(PROPERTY_FIELD_IDS.scope)}
              />

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

              {selected?.uses_binding && (
                <fieldset className="m-0 flex flex-col gap-4 border-0 p-0">
                  <legend className="mb-2 text-lg font-semibold">
                    Terminology binding
                  </legend>
                  <Select
                    id={PROPERTY_FIELD_IDS.bindingTarget}
                    label="Bound to"
                    placeholder="Choose what the property is bound to"
                    options={BINDING_TARGET_OPTIONS}
                    value={bindingTarget}
                    onChange={(event) => set("bindingTarget", event.target.value)}
                    error={errorFor(PROPERTY_FIELD_IDS.bindingTarget)}
                  />

                  {bindingTarget === VALUE_SET_TARGET && (
                    <>
                      <Field
                        id={PROPERTY_FIELD_IDS.valueSetUri}
                        label="Value set URI"
                        error={errorFor(PROPERTY_FIELD_IDS.valueSetUri)}
                      >
                        {(controlProps) => (
                          <input
                            {...controlProps}
                            type="text"
                            className="font-mono"
                            value={values.valueSetUri}
                            onChange={(event) => set("valueSetUri", event.target.value)}
                          />
                        )}
                      </Field>
                      <Select
                        id={PROPERTY_FIELD_IDS.strength}
                        label="Binding strength"
                        placeholder="Choose a strength"
                        options={BINDING_STRENGTH_OPTIONS}
                        value={values.strength}
                        onChange={(event) => set("strength", event.target.value)}
                        error={errorFor(PROPERTY_FIELD_IDS.strength)}
                      />
                      <Field
                        id={PROPERTY_FIELD_IDS.edition}
                        label="Edition"
                        hint="The SNOMED CT edition the value set belongs to, for example au."
                        error={errorFor(PROPERTY_FIELD_IDS.edition)}
                      >
                        {(controlProps) => (
                          <input
                            {...controlProps}
                            type="text"
                            className="font-mono"
                            value={values.edition}
                            onChange={(event) => set("edition", event.target.value)}
                          />
                        )}
                      </Field>
                    </>
                  )}

                  {bindingTarget === LOCAL_CODE_SYSTEM_TARGET && (
                    <Field
                      id={PROPERTY_FIELD_IDS.localCodeSystemKey}
                      label="Local code system key"
                      error={errorFor(PROPERTY_FIELD_IDS.localCodeSystemKey)}
                    >
                      {(controlProps) => (
                        <input
                          {...controlProps}
                          type="text"
                          className="font-mono"
                          value={values.localCodeSystemKey}
                          onChange={(event) =>
                            set("localCodeSystemKey", event.target.value)
                          }
                        />
                      )}
                    </Field>
                  )}
                </fieldset>
              )}

              <Field
                id={PROPERTY_FIELD_IDS.constraints}
                label="Constraints"
                hint={
                  selected
                    ? allowedConstraintKeys.length > 0
                      ? `Optional. A JSON object using only these names: ${allowedConstraintKeys.join(", ")}.`
                      : "Optional. This datatype takes no constraints, so leave it empty."
                    : "Optional. A JSON object. Choose a datatype to see which names it accepts."
                }
                error={errorFor(PROPERTY_FIELD_IDS.constraints)}
              >
                {(controlProps) => (
                  <textarea
                    {...controlProps}
                    rows={3}
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
        )}
      </PageContainer>
    </section>
  );
}
