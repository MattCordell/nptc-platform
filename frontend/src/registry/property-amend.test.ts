import { describe, expect, it } from "vitest";

import {
  amendValuesFrom,
  buildAmendRequest,
  rebaseOnto,
  type AmendValues,
} from "./property-amend.ts";
import { PROPERTY_FIELD_IDS } from "./property-form.ts";

/**
 * The amend form's request body (FR-09, FR-12). The API refuses an explicit
 * null and any field it does not amend, so the body must carry only what the
 * editor changed.
 */

const DEFINITION = {
  key: "assay_method",
  label: "Assay method",
  datatype: "alpha",
  cardinality: "0..1",
  scope: "both",
  required_for_submission: false,
  required_for_publication: true,
  binding_target: null,
  value_set_uri: null,
  strength: null,
  edition: null,
  local_code_system_key: null,
  filterable: false,
  origin: "admin",
  status: "active",
  display_order: 20,
  constraints: { maxLength: 80 },
  row_version: 3,
  form_control: { control: "text" as const, params: {} },
};

const INITIAL = amendValuesFrom(DEFINITION);

function edited(overrides: Partial<AmendValues>): AmendValues {
  return { ...INITIAL, reason: "Clarify the label", ...overrides };
}

describe("amendValuesFrom", () => {
  it("fills the form from the definition, with the constraints as readable JSON", () => {
    expect(INITIAL).toEqual({
      label: "Assay method",
      displayOrder: "20",
      requiredForSubmission: false,
      requiredForPublication: true,
      filterable: false,
      constraintsText: '{\n  "maxLength": 80\n}',
      reason: "",
    });
  });

  it("leaves the constraints box empty when there are none", () => {
    expect(amendValuesFrom({ ...DEFINITION, constraints: {} }).constraintsText).toBe("");
  });
});

describe("rebaseOnto", () => {
  const LATEST = amendValuesFrom({
    ...DEFINITION,
    label: "Assay method v2",
    display_order: 30,
    filterable: true,
    row_version: 4,
  });

  it("gives every untouched field the new value and keeps the reason", () => {
    const { values, initial } = rebaseOnto(
      { ...INITIAL, reason: "Because" },
      INITIAL,
      LATEST,
    );

    expect(values).toEqual({ ...LATEST, reason: "Because" });
    expect(initial).toBe(LATEST);
  });

  it("keeps a field the editor changed and takes the new value for the rest", () => {
    const { values } = rebaseOnto(edited({ displayOrder: "5" }), INITIAL, LATEST);

    expect(values.displayOrder).toBe("5");
    expect(values.label).toBe("Assay method v2");
    expect(values.filterable).toBe(true);
  });

  it("compares an edited field with the new value afterwards", () => {
    const rebased = rebaseOnto(edited({ displayOrder: "30" }), INITIAL, LATEST);

    // The editor's 30 now equals the server's 30, so there is nothing to send.
    expect(buildAmendRequest(rebased.values, rebased.initial, 4).body).toBeNull();
  });

  it("counts typing the old value back as a change after someone else changed it", () => {
    // The form reloads first and shows the new label, then the editor types the old one.
    const rebased = rebaseOnto(edited({ displayOrder: "5" }), INITIAL, LATEST);
    const typedBack = { ...rebased.values, label: "Assay method" };

    expect(buildAmendRequest(typedBack, rebased.initial, 4).body).toEqual({
      display_order: 5,
      label: "Assay method",
      expected_row_version: 4,
      reason: "Clarify the label",
    });
  });

  it("sends nothing for fields the editor did not touch, so the other change stands", () => {
    const rebased = rebaseOnto(edited({ displayOrder: "5" }), INITIAL, LATEST);

    expect(buildAmendRequest(rebased.values, rebased.initial, 4).body).toEqual({
      display_order: 5,
      expected_row_version: 4,
      reason: "Clarify the label",
    });
  });
});

describe("buildAmendRequest", () => {
  it("sends only the fields that changed, with the row version and reason", () => {
    const result = buildAmendRequest(
      edited({ label: " Assay method (immunoassay) " }),
      INITIAL,
      3,
    );

    expect(result.errors).toEqual([]);
    expect(result.body).toEqual({
      label: "Assay method (immunoassay)",
      expected_row_version: 3,
      reason: "Clarify the label",
    });
  });

  it("sends every kind of change at once", () => {
    const result = buildAmendRequest(
      edited({
        displayOrder: "5",
        requiredForSubmission: true,
        requiredForPublication: false,
        filterable: true,
        constraintsText: '{"maxLength": 120}',
      }),
      INITIAL,
      3,
    );

    expect(result.body).toEqual({
      display_order: 5,
      required_for_submission: true,
      required_for_publication: false,
      filterable: true,
      constraints: { maxLength: 120 },
      expected_row_version: 3,
      reason: "Clarify the label",
    });
  });

  it("sends an empty object, not null, when the constraints are cleared", () => {
    const result = buildAmendRequest(edited({ constraintsText: "" }), INITIAL, 3);

    expect(result.body).toMatchObject({ constraints: {} });
  });

  it("does not send constraints that were only reformatted", () => {
    const result = buildAmendRequest(
      edited({ label: "New", constraintsText: '{"maxLength":80}' }),
      INITIAL,
      3,
    );

    expect(result.body).not.toHaveProperty("constraints");
  });

  it("uses the row version it is given, not the one the form was filled from", () => {
    const result = buildAmendRequest(edited({ label: "New" }), INITIAL, 4);

    expect(result.body).toMatchObject({ expected_row_version: 4 });
  });

  it("never sends the fields the API refuses to amend", () => {
    const result = buildAmendRequest(edited({ label: "New" }), INITIAL, 3);

    for (const field of ["key", "datatype", "cardinality", "scope", "binding_target"]) {
      expect(result.body).not.toHaveProperty(field);
    }
  });

  it("refuses a save that changes nothing, naming the first field", () => {
    const result = buildAmendRequest(edited({}), INITIAL, 3);

    expect(result.body).toBeNull();
    expect(result.errors).toEqual([
      {
        fieldId: PROPERTY_FIELD_IDS.label,
        message: "Change at least one field before saving.",
      },
    ]);
  });

  it("refuses an empty label, a bad display order, bad constraints and no reason together", () => {
    const result = buildAmendRequest(
      {
        ...INITIAL,
        label: " ",
        displayOrder: "soon",
        constraintsText: "[1]",
        reason: "",
      },
      INITIAL,
      3,
    );

    expect(result.body).toBeNull();
    expect(result.errors.map((error) => error.fieldId)).toEqual([
      PROPERTY_FIELD_IDS.label,
      PROPERTY_FIELD_IDS.displayOrder,
      PROPERTY_FIELD_IDS.constraints,
      PROPERTY_FIELD_IDS.reason,
    ]);
  });

  it("refuses a reason that is only spaces", () => {
    const result = buildAmendRequest(edited({ label: "New", reason: "  " }), INITIAL, 3);

    expect(result.errors.map((error) => error.fieldId)).toEqual([
      PROPERTY_FIELD_IDS.reason,
    ]);
  });
});
