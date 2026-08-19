import { describe, expect, it } from "vitest";

import { createAppRouter } from "./router.tsx";

/**
 * Exercises `router.tsx`'s hand-rolled `parseSearch`/`stringifySearch`
 * directly (via the created router's own options, since neither function is
 * exported on its own) - the repeated-key and non-scalar-value branches
 * aren't reached by any route this issue ships, but the functions are
 * general-purpose and must behave correctly (or fail loudly) regardless.
 */
describe("parseSearch", () => {
  it("collects a repeated key into an array, preserving raw string values", () => {
    const { parseSearch } = createAppRouter().options;
    expect(parseSearch("?property=a&property=b&property=c")).toEqual({
      property: ["a", "b", "c"],
    });
  });

  it("keeps a once-only key as a scalar, not a single-element array", () => {
    const { parseSearch } = createAppRouter().options;
    expect(parseSearch("?q=glucose")).toEqual({ q: "glucose" });
  });
});

describe("stringifySearch", () => {
  it("round-trips a repeated-key array back into the same query string shape", () => {
    const { parseSearch, stringifySearch } = createAppRouter().options;
    const search = parseSearch("?property=a&property=b");
    expect(stringifySearch(search)).toBe("?property=a&property=b");
  });

  // Only scalars and arrays of scalars are supported (see the doc comment on
  // `stringifySearch` in router.tsx for why a plain object can't round-trip
  // through `parseSearch`, which never JSON-parses a value back out).
  it("throws for a non-scalar object value, rather than silently degrading it", () => {
    const { stringifySearch } = createAppRouter().options;
    expect(() => stringifySearch({ filter: { discipline: "Haematology" } })).toThrow(
      /non-scalar/,
    );
  });

  it("throws for a non-scalar item inside an array value", () => {
    const { stringifySearch } = createAppRouter().options;
    expect(() => stringifySearch({ filters: [{ discipline: "Haematology" }] })).toThrow(
      /non-scalar/,
    );
  });
});
