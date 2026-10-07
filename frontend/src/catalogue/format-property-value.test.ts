import { describe, expect, it } from "vitest";

import { describePropertyValue, formatPropertyValue } from "./format-property-value.ts";

const SNOMED = "http://snomed.info/sct";
const LOCAL = "https://nptc.example.org/CodeSystem/discipline";
// Past Number.MAX_SAFE_INTEGER, so a coerced code would differ (FR-06).
const LONG_CODE = "999480561000168100";

describe("formatPropertyValue", () => {
  it.each([
    ["text", "000123", "000123"],
    ["a number", 12, "12"],
    ["a boolean", false, "false"],
    ["an object", { a: 1 }, '{"a":1}'],
    ["null", null, "null"],
  ])("formats %s", (_label, value, expected) => {
    expect(formatPropertyValue(value)).toBe(expected);
  });

  it("returns a string untouched", () => {
    expect(formatPropertyValue(LONG_CODE)).toBe(LONG_CODE);
  });
});

describe("describePropertyValue", () => {
  it("shows the term and the code for a SNOMED CT coded value", () => {
    expect(
      describePropertyValue({ code: LONG_CODE, system: SNOMED, display: "Urine" }),
    ).toEqual({ text: "Urine", snomedCode: LONG_CODE });
  });

  it("keeps a SNOMED CT code exactly, with its leading zeros", () => {
    expect(
      describePropertyValue({ code: "000123", system: SNOMED, display: "Urine" })
        .snomedCode,
    ).toBe("000123");
  });

  it("shows only the term for a local code", () => {
    expect(
      describePropertyValue({
        code: "chemical_pathology",
        system: LOCAL,
        display: "Chemical pathology",
      }),
    ).toEqual({ text: "Chemical pathology", snomedCode: null });
  });

  it("shows the code chip alone for a SNOMED CT value with no term", () => {
    expect(
      describePropertyValue({ code: LONG_CODE, system: SNOMED, display: null }),
    ).toEqual({
      text: "",
      snomedCode: LONG_CODE,
    });
    expect(describePropertyValue({ code: LONG_CODE, system: SNOMED })).toEqual({
      text: "",
      snomedCode: LONG_CODE,
    });
  });

  it("falls back to the code as text for a local value with no term", () => {
    expect(describePropertyValue({ code: "x", system: LOCAL, display: null })).toEqual({
      text: "x",
      snomedCode: null,
    });
  });

  it.each([
    ["text", "plain", "plain"],
    ["a number", 12, "12"],
    ["an object of another shape", { a: 1 }, '{"a":1}'],
    [
      "an object with a numeric code",
      { code: 123, system: SNOMED },
      '{"code":123,"system":"http://snomed.info/sct"}',
    ],
    ["an array", [1, 2], "[1,2]"],
  ])("formats %s as plain text with no code chip", (_label, value, text) => {
    expect(describePropertyValue(value)).toEqual({ text, snomedCode: null });
  });
});
