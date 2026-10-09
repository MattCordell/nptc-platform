import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { termLength } from "../src/catalogue/term-length.ts";

/**
 * FR-85 / ADR-0030: the browser's term length must equal the server's for
 * every case in the shared fixture. `backend/tests/test_term_length_fixtures.py`
 * reads the same file and asserts `preferred_term_length`, so a change to one
 * side that the other does not follow fails here or there.
 */

interface LengthCase {
  name: string;
  term: string;
  length: number;
}

const FIXTURE = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../shared/tests/fixtures/term-length-cases.json",
);
const cases = JSON.parse(readFileSync(FIXTURE, "utf-8")) as LengthCase[];

describe("term length mirror (shared fixture)", () => {
  it("loads a non-trivial fixture", () => {
    expect(cases.length).toBeGreaterThan(10);
  });

  it.each(cases)("$name", ({ term, length }) => {
    expect(termLength(term)).toBe(length);
  });
});
