/**
 * The Python text rules the browser mirrors under ADR-0030, written once.
 *
 * `nptc_shared.text.normalise_for_comparison` is the server-side original.
 * `split-synonyms.ts`, `changelog-note.ts` and `term-length.ts` all depend on
 * it, so the three cannot drift apart on whitespace.
 *
 * Every character is an escape, never the character itself: a source file that
 * contains an invisible character is the defect class this platform exists to
 * eliminate.
 */

/**
 * The inner content of the character class `str.strip()` and `\s` use in
 * Python. `String.prototype.trim()` is not that set: Python strips U+0085 and
 * U+001C-U+001F, which JavaScript leaves, and JavaScript trims U+FEFF, which
 * Python does not.
 */
export const PYTHON_SPACE_CHARS =
  "\\t\\n\\v\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";

export const PYTHON_SPACE_CLASS = `[${PYTHON_SPACE_CHARS}]`;

const PYTHON_EDGE_WHITESPACE = new RegExp(
  `^${PYTHON_SPACE_CLASS}+|${PYTHON_SPACE_CLASS}+$`,
  "gu",
);

/** Every Unicode `Zs` space except the ordinary one, as
 * `nptc_shared.text.is_normalisable_space` matches them. */
const NON_ASCII_ZS_SPACE = new RegExp(
  "[\\u00a0\\u1680\\u2000-\\u200a\\u202f\\u205f\\u3000]",
  "gu",
);

/** `str.strip()`, not `trim()`. */
export function pythonStrip(text: string): string {
  return text.replace(PYTHON_EDGE_WHITESPACE, "");
}

/**
 * Mirrors `nptc_shared.text.normalise_for_comparison`: NFC, every non-ASCII
 * `Zs` space collapsed to an ordinary space wherever it occurs, then edge
 * whitespace stripped.
 */
export function normaliseForComparison(text: string): string {
  const composed = text.normalize("NFC");
  return pythonStrip(composed.replace(NON_ASCII_ZS_SPACE, " "));
}
