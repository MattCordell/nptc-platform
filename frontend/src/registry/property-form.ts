import type { components } from "../api/schema.ts";
import type { FormError } from "../components/error-summary.tsx";

/**
 * Client-side checks for the property create form (FR-09, FR-12). The API
 * answers some bad requests with a 500 because the database rejects them
 * (a malformed key, or a binding that does not match the datatype), so the form
 * catches those before sending.
 *
 * Nothing here names a datatype. Whether a datatype takes a binding comes from
 * `DatatypeDescription.uses_binding`, and the constraint keys it accepts come
 * from its JSON Schema (FR-77, ADR-0013).
 */

type CreateBody = components["schemas"]["CreatePropertyDefinitionRequest"];
export type DatatypeDescription = components["schemas"]["DatatypeDescription"];

/** The key rule `property_definition`'s database `CHECK` enforces. */
export const PROPERTY_KEY_PATTERN = /^[a-z][a-z0-9_]{0,62}$/;

export const PROPERTY_FIELD_IDS = {
  key: "property-key",
  label: "property-label",
  datatype: "property-datatype",
  cardinality: "property-cardinality",
  scope: "property-scope",
  displayOrder: "property-display-order",
  bindingTarget: "property-binding-target",
  valueSetUri: "property-value-set-uri",
  strength: "property-strength",
  edition: "property-edition",
  localCodeSystemKey: "property-local-code-system",
  constraints: "property-constraints",
  reason: "property-reason",
} as const;

export type CreateValues = {
  key: string;
  label: string;
  datatype: string;
  cardinality: string;
  scope: string;
  displayOrder: string;
  requiredForSubmission: boolean;
  requiredForPublication: boolean;
  filterable: boolean;
  bindingTarget: string;
  valueSetUri: string;
  strength: string;
  edition: string;
  localCodeSystemKey: string;
  constraintsText: string;
  reason: string;
};

export const EMPTY_CREATE_VALUES: CreateValues = {
  key: "",
  label: "",
  datatype: "",
  cardinality: "",
  scope: "",
  displayOrder: "",
  requiredForSubmission: false,
  requiredForPublication: false,
  filterable: false,
  bindingTarget: "",
  valueSetUri: "",
  strength: "",
  edition: "",
  localCodeSystemKey: "",
  constraintsText: "",
  reason: "",
};

export const VALUE_SET_TARGET = "value_set";
export const LOCAL_CODE_SYSTEM_TARGET = "local_code_system";

type ConstraintsParse =
  { ok: true; value: Record<string, unknown> } | { ok: false; message: string };

/** An empty box means no constraints. Anything else must be one JSON object. */
export function parseConstraints(text: string): ConstraintsParse {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { ok: true, value: {} };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return {
      ok: false,
      message: "Constraints are not valid JSON. Check the braces, quotes and commas.",
    };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return {
      ok: false,
      message: 'Constraints must be one JSON object, for example {"name": value}.',
    };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

/** An empty box means 0, the API's default. */
export function parseDisplayOrder(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return 0;
  }
  if (!/^\d+$/.test(trimmed)) {
    return null;
  }
  const value = Number(trimmed);
  return Number.isSafeInteger(value) ? value : null;
}

/** The constraint names a datatype's schema allows, read without interpreting any. */
export function constraintKeysOf(schema: Record<string, unknown>): string[] {
  const properties = schema.properties;
  return typeof properties === "object" && properties !== null
    ? Object.keys(properties)
    : [];
}

export function reasonProblem(text: string, action: string): string | null {
  return text.trim().length === 0 ? `Enter the reason for ${action}.` : null;
}

function problem(fieldId: string, message: string): FormError {
  return { fieldId, message };
}

type BindingFields = Pick<
  CreateBody,
  "binding_target" | "value_set_uri" | "strength" | "edition" | "local_code_system_key"
>;

function bindingFor(
  values: CreateValues,
  errors: FormError[],
): BindingFields | Record<string, never> {
  const target = values.bindingTarget;
  if (target === VALUE_SET_TARGET) {
    const valueSetUri = values.valueSetUri.trim();
    const edition = values.edition.trim();
    if (valueSetUri.length === 0) {
      errors.push(problem(PROPERTY_FIELD_IDS.valueSetUri, "Enter the value set URI."));
    }
    if (values.strength.length === 0) {
      errors.push(problem(PROPERTY_FIELD_IDS.strength, "Choose the binding strength."));
    }
    if (edition.length === 0) {
      errors.push(
        problem(
          PROPERTY_FIELD_IDS.edition,
          "Enter the SNOMED CT edition the value set belongs to, for example au.",
        ),
      );
    }
    return {
      binding_target: target,
      value_set_uri: valueSetUri,
      strength: values.strength as BindingFields["strength"],
      edition,
    };
  }
  if (target === LOCAL_CODE_SYSTEM_TARGET) {
    const systemKey = values.localCodeSystemKey.trim();
    if (systemKey.length === 0) {
      errors.push(
        problem(
          PROPERTY_FIELD_IDS.localCodeSystemKey,
          "Enter the key of the local code system.",
        ),
      );
    }
    return { binding_target: target, local_code_system_key: systemKey };
  }
  errors.push(
    problem(PROPERTY_FIELD_IDS.bindingTarget, "Choose what the property is bound to."),
  );
  return {};
}

export type CreateRequestResult = { errors: FormError[]; body: CreateBody | null };

/**
 * Checks every field and, when all pass, builds the request body. The four
 * flag and ordering fields are always sent: the generated body type marks them
 * required even though the API defaults them.
 */
export function buildCreateRequest(
  values: CreateValues,
  datatype: DatatypeDescription | undefined,
): CreateRequestResult {
  const errors: FormError[] = [];
  const key = values.key.trim();
  const label = values.label.trim();

  if (!PROPERTY_KEY_PATTERN.test(key)) {
    errors.push(
      problem(
        PROPERTY_FIELD_IDS.key,
        "Enter a key of lowercase letters, digits and underscores, starting with a letter, up to 63 characters.",
      ),
    );
  }
  if (label.length === 0) {
    errors.push(problem(PROPERTY_FIELD_IDS.label, "Enter a label."));
  }
  if (datatype === undefined) {
    errors.push(problem(PROPERTY_FIELD_IDS.datatype, "Choose a datatype."));
  }
  if (values.cardinality.length === 0) {
    errors.push(
      problem(
        PROPERTY_FIELD_IDS.cardinality,
        "Choose how many values an entry can hold.",
      ),
    );
  }
  if (values.scope.length === 0) {
    errors.push(problem(PROPERTY_FIELD_IDS.scope, "Choose where the property applies."));
  }
  const displayOrder = parseDisplayOrder(values.displayOrder);
  if (displayOrder === null) {
    errors.push(
      problem(
        PROPERTY_FIELD_IDS.displayOrder,
        "Enter the display order as a whole number, or leave it empty for 0.",
      ),
    );
  }
  const constraints = parseConstraints(values.constraintsText);
  if (!constraints.ok) {
    errors.push(problem(PROPERTY_FIELD_IDS.constraints, constraints.message));
  }
  const binding = datatype?.uses_binding ? bindingFor(values, errors) : {};
  const reason = values.reason.trim();
  const missingReason = reasonProblem(values.reason, "creating this property");
  if (missingReason !== null) {
    errors.push(problem(PROPERTY_FIELD_IDS.reason, missingReason));
  }

  if (
    errors.length > 0 ||
    datatype === undefined ||
    displayOrder === null ||
    !constraints.ok
  ) {
    return { errors, body: null };
  }
  return {
    errors,
    body: {
      key,
      label,
      datatype: datatype.name,
      cardinality: values.cardinality as CreateBody["cardinality"],
      scope: values.scope as CreateBody["scope"],
      required_for_submission: values.requiredForSubmission,
      required_for_publication: values.requiredForPublication,
      filterable: values.filterable,
      display_order: displayOrder,
      constraints: constraints.value,
      ...binding,
      reason,
    },
  };
}
