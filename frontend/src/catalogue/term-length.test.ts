import { describe, expect, it } from "vitest";

import { termLength } from "./term-length.ts";

describe("termLength", () => {
  it("counts a plain term", () => {
    expect(termLength("Full blood count")).toBe(16);
  });

  it("counts a character outside the Basic Multilingual Plane once", () => {
    // Two UTF-16 units, one code point - the server counts one.
    expect("\u{1F9EA}".length).toBe(2);
    expect(termLength("\u{1F9EA}")).toBe(1);
  });

  it("counts after the whitespace cleaning the server applies", () => {
    expect(termLength("Ferritin ")).toBe(8);
  });
});
