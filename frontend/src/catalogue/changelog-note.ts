/**
 * FR-37 changelog note validation (issue #62's client-side gate).
 *
 * A deliberate, tested mirror of `validate_changelog_note` in
 * `backend/src/nptc/catalogue/changelog.py` (issue #47) - see
 * `docs/adr/0030-domain-logic-at-the-browser-boundary.md` for why this is
 * duplicated rather than shared, and what keeps the two honest.
 *
 * The server's 422 (`api/errors.py`'s `_DETAIL_CHANGELOG_NOTE`) is a single
 * generic sentence that never says which FR-37 rule failed, so this mirror
 * exists to give the editor that rule-specific guidance *before* submit
 * rather than only a refusal after it. The server remains the authority
 * (NFR-20): this module never persists anything, and a request that bypasses
 * it is still rejected server-side.
 */

//: Mirrors `MINIMUM_NOTE_LENGTH` in `changelog.py`.
export const MINIMUM_NOTE_LENGTH = 10;

//: Mirrors `LOW_INFORMATION_NOTES` in `changelog.py`, verbatim.
const LOW_INFORMATION_NOTES = new Set<string>([
  "update",
  "updated",
  "fix",
  "fixed",
  "change",
  "changed",
  "edit",
  "edited",
  "correction",
  "corrected",
  "typo",
  "minor",
  "minor update",
  "minor change",
  "as discussed",
  "as agreed",
  "n a",
  "na",
  "none",
  "test",
  "wip",
  "tidy up",
  "cleanup",
  "misc",
  "",
]);

/**
 * Every codepoint Unicode classifies as general category `Zs` other than
 * the ordinary space - the set `nptc_shared.text.is_normalisable_space`
 * matches. Written as escapes, never as the characters themselves, the same
 * posture `split-synonyms.ts`'s `PYTHON_SPACE_CLASS` takes for a related but
 * wider set: a source file containing an invisible character is the defect
 * class this platform exists to eliminate.
 */
const NON_ASCII_ZS_SPACE = new RegExp(
  "[\\u00a0\\u1680\\u2000-\\u200a\\u202f\\u205f\\u3000]",
  "gu",
);

//: The same Python `str.strip()` character class `split-synonyms.ts`
//: defines as `PYTHON_SPACE_CLASS`, reused here for the edge-trim
//: `normalise_for_comparison` applies after collapsing `Zs` spaces - see
//: that file for why `String.prototype.trim()` is not equivalent.
const PYTHON_SPACE_CLASS =
  "[\\t\\n\\v\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000]";
const PYTHON_EDGE_WHITESPACE = new RegExp(
  `^${PYTHON_SPACE_CLASS}+|${PYTHON_SPACE_CLASS}+$`,
  "gu",
);

/**
 * Mirrors `nptc_shared.text.normalise_for_comparison`: NFC, every
 * non-ASCII `Zs` space character collapsed to an ordinary space wherever
 * it occurs, then edge whitespace stripped using Python's (wider)
 * `str.strip()` class.
 */
function normaliseForComparison(text: string): string {
  const composed = text.normalize("NFC");
  const collapsed = composed.replace(NON_ASCII_ZS_SPACE, " ");
  return collapsed.replace(PYTHON_EDGE_WHITESPACE, "");
}

//: Mirrors `_STRIP_PUNCTUATION_RE` (`[^\w\s]`, Unicode). Python's Unicode
//: `\w` is letters, digits and underscore; `\p{L}\p{N}_` is the closest
//: JavaScript equivalent. Every `LOW_INFORMATION_NOTES` entry is plain
//: ASCII, so this only needs to agree with Python on the boundary of
//: stripping punctuation around those words, not on the full Unicode `\w`
//: definition.
const STRIP_PUNCTUATION_RE = /[^\p{L}\p{N}_\s]/gu;
//: Mirrors `_HAS_LETTER_RE` (`[^\W\d_]`, Unicode) - true if the note has no
//: letter at all.
const HAS_LETTER_RE = /\p{L}/u;

/**
 * Mirrors `_fold`: casefolded, punctuation-stripped, whitespace-collapsed
 * form used only for matching against `LOW_INFORMATION_NOTES`.
 *
 * JavaScript has no full Unicode casefold; `toLowerCase()` is used instead.
 * Every list entry is ASCII lowercase already, so this only matters for a
 * note containing non-ASCII characters that fold differently under
 * `casefold()` than under `toLowerCase()` - none of the shared fixtures
 * exercise that case, and the divergence is the same kind ADR-0030 flags
 * as a standing, accepted cost of mirroring.
 */
function fold(note: string): string {
  const stripped = note.replace(STRIP_PUNCTUATION_RE, "");
  return stripped.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

export type ChangelogNoteResult =
  | { status: "empty"; message: string }
  | { status: "low-information"; message: string }
  | { status: "too-short"; message: string }
  | { status: "no-letter"; message: string }
  | { status: "ok"; note: string };

/**
 * Validates `note` against FR-37, mirroring `validate_changelog_note`'s
 * check order exactly: empty, then low-information (before length, so a
 * note like `"fix"` gets the more specific message), then length, then
 * "contains a letter".
 */
export function validateChangelogNote(
  note: string | null | undefined,
): ChangelogNoteResult {
  if (note === null || note === undefined) {
    return { status: "empty", message: "A changelog note is required." };
  }

  const normalised = normaliseForComparison(note);
  if (!normalised) {
    return { status: "empty", message: "A changelog note is required." };
  }

  if (LOW_INFORMATION_NOTES.has(fold(normalised))) {
    return {
      status: "low-information",
      message:
        "This note becomes the published History text - describe what actually changed, " +
        `not "${normalised}".`,
    };
  }

  if (normalised.length < MINIMUM_NOTE_LENGTH) {
    return {
      status: "too-short",
      message: `A changelog note must be at least ${MINIMUM_NOTE_LENGTH} characters and describe the change.`,
    };
  }

  if (!HAS_LETTER_RE.test(normalised)) {
    return {
      status: "no-letter",
      message: "A changelog note must contain a letter and describe the change.",
    };
  }

  return { status: "ok", note: normalised };
}
