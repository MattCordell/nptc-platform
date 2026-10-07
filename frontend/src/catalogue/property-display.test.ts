import { describe, expect, it } from "vitest";

import {
  bindingStrengthLabelFor,
  bindingTargetLabelFor,
  cardinalityLabelFor,
  constraintValueText,
  originLabelFor,
  scopeLabelFor,
} from "./property-display.ts";

describe("property display labels", () => {
  it("labels every value the API documents", () => {
    expect(["submission", "maintenance", "both"].map(scopeLabelFor)).toEqual([
      "Submission",
      "Maintenance",
      "Both",
    ]);
    expect(["0..1", "1..1", "0..*", "1..*"].map(cardinalityLabelFor)).toEqual([
      "Zero or one",
      "Exactly one",
      "Zero or more",
      "One or more",
    ]);
    expect(["system", "admin"].map(originLabelFor)).toEqual(["System", "Administrator"]);
    expect(["value_set", "local_code_system"].map(bindingTargetLabelFor)).toEqual([
      "Value set",
      "Local code system",
    ]);
    expect(["required", "extensible", "example"].map(bindingStrengthLabelFor)).toEqual([
      "Required",
      "Extensible",
      "Example",
    ]);
  });

  it("falls back to the raw value for one it does not list", () => {
    expect(scopeLabelFor("archive")).toBe("archive");
    expect(cardinalityLabelFor("2..5")).toBe("2..5");
    expect(originLabelFor("import")).toBe("import");
    expect(bindingTargetLabelFor("map")).toBe("map");
    expect(bindingStrengthLabelFor("preferred")).toBe("preferred");
  });
});

describe("constraintValueText", () => {
  it("shows a string as itself and anything else as JSON", () => {
    expect(constraintValueText("mg")).toBe("mg");
    expect(constraintValueText(200)).toBe("200");
    expect(constraintValueText(["https", "http"])).toBe('["https","http"]');
    expect(constraintValueText(null)).toBe("null");
  });
});
