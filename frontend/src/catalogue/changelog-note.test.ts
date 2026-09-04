/**
 * Fixtures quoted verbatim from `backend/tests/test_changelog_note.py`,
 * the Python counterpart this file mirrors (ADR-0030 condition 2).
 */
import { describe, expect, it } from "vitest";

import { MINIMUM_NOTE_LENGTH, validateChangelogNote } from "./changelog-note";

//: Built via `String.fromCharCode()`, not a literal non-ASCII space, per
//: this repo's own convention (see `split-synonyms.ts`).
const NBSP = String.fromCharCode(0x00a0);

describe("validateChangelogNote", () => {
  it.each([null, undefined, "", "   ", NBSP.repeat(3)])(
    "rejects an empty or invisible-only note: %p",
    (note) => {
      expect(validateChangelogNote(note).status).toBe("empty");
    },
  );

  it.each([
    "update",
    "Update",
    "UPDATE.",
    "fix",
    "Fixed",
    "minor update",
    "as discussed",
  ])("rejects a low-information note before the length check: %p", (note) => {
    // These all match the low-information list and are also short enough
    // to fail the length floor - "low-information" is expected because that
    // check runs first (mirrors changelog.py's own check order).
    expect(validateChangelogNote(note).status).toBe("low-information");
  });

  it.each(["2026", "12345", "00:00"])(
    "rejects a short letterless note for length, not missing-letter: %p",
    (note) => {
      // Too short *and* letterless, but not on the low-information list
      // (that list's "."/"n/a"/"---" equivalents all fold to an empty or
      // short punctuation-stripped string already in the set). The length
      // check runs before the letter check, so this is "too-short".
      expect(validateChangelogNote(note).status).toBe("too-short");
    },
  );

  it.each(["1234567890123", "2026-08-20 00:00"])(
    "rejects a letterless note past the length floor for missing letter: %p",
    (note) => {
      expect(note.length).toBeGreaterThanOrEqual(MINIMUM_NOTE_LENGTH);
      expect(validateChangelogNote(note).status).toBe("no-letter");
    },
  );

  it("rejects a note padded with non-breaking spaces past the length floor", () => {
    // A naive length check would count the non-breaking-space padding as
    // real characters and let a low-information note squeak past the
    // minimum - normaliseForComparison collapses and strips them first.
    const padded = "fix" + NBSP.repeat(10);
    expect(validateChangelogNote(padded).status).toBe("low-information");
  });

  it("rejects a short but not low-information note for length", () => {
    expect(validateChangelogNote("ok done").status).toBe("too-short");
  });

  it("accepts and normalises a meaningful note", () => {
    const result = validateChangelogNote(
      "Corrected the specimen for the RBC assay" + NBSP,
    );
    expect(result).toEqual({
      status: "ok",
      note: "Corrected the specimen for the RBC assay",
    });
  });

  it("folds on Python's wider whitespace, not JavaScript's narrower \\s (issue #62 review)", () => {
    // U+001E (record separator) has Python's `str.isspace()` true (and so is
    // a word boundary for `str.split()`) but is not JavaScript `\s` - before
    // this was fixed, `fold` stripped it as punctuation and concatenated
    // across it, matching "fixed" on the low-information list even though
    // Python sees two words ("fix", "ed") and does not.
    const note = "fix" + String.fromCharCode(0x1e) + "ed";
    expect(validateChangelogNote(note).status).toBe("too-short");
  });

  it("counts a vulgar fraction as a letter, matching Python's \\w (issue #62 review)", () => {
    // U+00BD ("½") is Unicode category `No`; Python's `[^\W\d_]` counts it as
    // a word character the same way it counts a Roman numeral like "Ⅷ"
    // (category `Nl`) - both pass `str.isalnum()` without being a decimal
    // digit. `\p{L}` alone does not include either category.
    const note = "½".repeat(MINIMUM_NOTE_LENGTH);
    expect(validateChangelogNote(note)).toEqual({ status: "ok", note });
  });
});
