import { describe, expect, it } from "vitest";

import {
  buildCreateRequest,
  constraintKeysOf,
  CREATE_FIELD_IDS,
  type CreateValues,
  type DatatypeDescription,
  EMPTY_CREATE_VALUES,
  parseConstraints,
  parseDisplayOrder,
  PROPERTY_KEY_PATTERN,
} from "./property-form.ts";

/**
 * The create form's own checks (FR-09, FR-12). Each failure case is one the
 * API would answer with a 500 or a 422 that names no field, so the form has to
 * catch it before sending.
 */

const PLAIN: DatatypeDescription = {
  name: "alpha",
  constraints_schema: { properties: { maxLength: { type: "integer" } } },
  uses_binding: false,
};
const BOUND: DatatypeDescription = {
  name: "beta",
  constraints_schema: {},
  uses_binding: true,
};

function values(overrides: Partial<CreateValues> = {}): CreateValues {
  return {
    ...EMPTY_CREATE_VALUES,
    key: "assay_method",
    label: "Assay method",
    datatype: "alpha",
    cardinality: "0..1",
    scope: "both",
    reason: "Needed for the immunoassay entries",
    ...overrides,
  };
}

function fieldIdsOf(result: ReturnType<typeof buildCreateRequest>): string[] {
  return result.errors.map((error) => error.fieldId);
}

describe("PROPERTY_KEY_PATTERN", () => {
  it.each(["a", "assay_method", "x1_2", "a".repeat(63)])("accepts %s", (key) => {
    expect(PROPERTY_KEY_PATTERN.test(key)).toBe(true);
  });

  it.each([
    "",
    "Assay",
    "1assay",
    "_assay",
    "assay-method",
    "assay method",
    "a".repeat(64),
  ])("rejects %j", (key) => {
    expect(PROPERTY_KEY_PATTERN.test(key)).toBe(false);
  });
});

describe("parseConstraints", () => {
  it("reads an empty box as no constraints", () => {
    expect(parseConstraints("  ")).toEqual({ ok: true, value: {} });
  });

  it("parses one JSON object", () => {
    expect(parseConstraints('{"maxLength": 200}')).toEqual({
      ok: true,
      value: { maxLength: 200 },
    });
  });

  it.each(["{", "maxLength: 200"])("refuses text that is not JSON: %s", (text) => {
    const result = parseConstraints(text);
    expect(result.ok).toBe(false);
  });

  it.each(["[1]", "3", "null", '"text"'])(
    "refuses JSON that is not an object: %s",
    (text) => {
      const result = parseConstraints(text);
      expect(result).toMatchObject({ ok: false });
      expect(result.ok ? "" : result.message).toContain("one JSON object");
    },
  );
});

describe("parseDisplayOrder", () => {
  it("reads an empty box as 0", () => {
    expect(parseDisplayOrder("")).toBe(0);
  });

  it("reads a whole number", () => {
    expect(parseDisplayOrder(" 40 ")).toBe(40);
  });

  it.each(["-1", "1.5", "ten", "1e3", "9".repeat(20)])("refuses %s", (text) => {
    expect(parseDisplayOrder(text)).toBeNull();
  });
});

describe("constraintKeysOf", () => {
  it("lists the names a schema allows without interpreting them", () => {
    expect(constraintKeysOf(PLAIN.constraints_schema)).toEqual(["maxLength"]);
  });

  it("returns none for a schema without properties", () => {
    expect(constraintKeysOf({})).toEqual([]);
    expect(constraintKeysOf({ properties: null })).toEqual([]);
  });
});

describe("buildCreateRequest", () => {
  it("builds the body for a property with no binding", () => {
    const result = buildCreateRequest(
      values({
        key: " assay_method ",
        displayOrder: "20",
        filterable: true,
        constraintsText: '{"maxLength": 80}',
        // Stale binding input is dropped when the datatype takes none.
        bindingTarget: "value_set",
        valueSetUri: "http://example.org/vs",
      }),
      PLAIN,
    );

    expect(result.errors).toEqual([]);
    expect(result.body).toEqual({
      key: "assay_method",
      label: "Assay method",
      datatype: "alpha",
      cardinality: "0..1",
      scope: "both",
      required_for_submission: false,
      required_for_publication: false,
      filterable: true,
      display_order: 20,
      constraints: { maxLength: 80 },
      reason: "Needed for the immunoassay entries",
    });
  });

  it("builds a value-set binding with its strength and edition", () => {
    const result = buildCreateRequest(
      values({
        datatype: "beta",
        bindingTarget: "value_set",
        valueSetUri: " http://snomed.info/sct?fhir_vs=ecl/%3C123038009 ",
        strength: "extensible",
        edition: "au",
        // A local key left over from an earlier choice is not sent.
        localCodeSystemKey: "stale",
      }),
      BOUND,
    );

    expect(result.errors).toEqual([]);
    expect(result.body).toMatchObject({
      binding_target: "value_set",
      value_set_uri: "http://snomed.info/sct?fhir_vs=ecl/%3C123038009",
      strength: "extensible",
      edition: "au",
    });
    expect(result.body).not.toHaveProperty("local_code_system_key");
  });

  it("builds a local code system binding", () => {
    const result = buildCreateRequest(
      values({
        datatype: "beta",
        bindingTarget: "local_code_system",
        localCodeSystemKey: "discipline",
        valueSetUri: "http://example.org/stale",
      }),
      BOUND,
    );

    expect(result.errors).toEqual([]);
    expect(result.body).toMatchObject({
      binding_target: "local_code_system",
      local_code_system_key: "discipline",
    });
    expect(result.body).not.toHaveProperty("value_set_uri");
  });

  it("reports every missing required field at once and builds no body", () => {
    const result = buildCreateRequest(EMPTY_CREATE_VALUES, undefined);

    expect(result.body).toBeNull();
    expect(fieldIdsOf(result)).toEqual([
      CREATE_FIELD_IDS.key,
      CREATE_FIELD_IDS.label,
      CREATE_FIELD_IDS.datatype,
      CREATE_FIELD_IDS.cardinality,
      CREATE_FIELD_IDS.scope,
      CREATE_FIELD_IDS.reason,
    ]);
  });

  it.each(["Assay", "1assay", "assay-method", "a".repeat(64)])(
    "refuses the key %j, which the database would reject with a 500",
    (key) => {
      const result = buildCreateRequest(values({ key }), PLAIN);

      expect(result.body).toBeNull();
      expect(fieldIdsOf(result)).toEqual([CREATE_FIELD_IDS.key]);
    },
  );

  it("refuses a datatype that takes a binding when none is chosen", () => {
    const result = buildCreateRequest(values({ datatype: "beta" }), BOUND);

    expect(result.body).toBeNull();
    expect(fieldIdsOf(result)).toEqual([CREATE_FIELD_IDS.bindingTarget]);
  });

  it("refuses a value-set binding missing its URI, strength or edition", () => {
    const result = buildCreateRequest(
      values({ datatype: "beta", bindingTarget: "value_set" }),
      BOUND,
    );

    expect(result.body).toBeNull();
    expect(fieldIdsOf(result)).toEqual([
      CREATE_FIELD_IDS.valueSetUri,
      CREATE_FIELD_IDS.strength,
      CREATE_FIELD_IDS.edition,
    ]);
  });

  it("refuses a local code system binding missing its key", () => {
    const result = buildCreateRequest(
      values({
        datatype: "beta",
        bindingTarget: "local_code_system",
        localCodeSystemKey: " ",
      }),
      BOUND,
    );

    expect(fieldIdsOf(result)).toEqual([CREATE_FIELD_IDS.localCodeSystemKey]);
  });

  it("refuses bad constraints and a bad display order together", () => {
    const result = buildCreateRequest(
      values({ constraintsText: "[1]", displayOrder: "soon" }),
      PLAIN,
    );

    expect(result.body).toBeNull();
    expect(fieldIdsOf(result)).toEqual([
      CREATE_FIELD_IDS.displayOrder,
      CREATE_FIELD_IDS.constraints,
    ]);
  });

  it("refuses a reason that is only spaces", () => {
    const result = buildCreateRequest(values({ reason: "   " }), PLAIN);

    expect(fieldIdsOf(result)).toEqual([CREATE_FIELD_IDS.reason]);
  });
});
