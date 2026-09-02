/**
 * Splitting a pasted synonym cell into individual terms (FR-04).
 *
 * A deliberate, tested mirror of `transform/src/nptc_transform/cell_defects.py`'s
 * `split_synonyms` - see `docs/adr/0029-domain-logic-at-the-browser-boundary.md`
 * for why this is duplicated rather than shared, and what keeps the two honest.
 *
 * `POST /catalogue/entries/{business_key}/designations` takes `terms` already
 * split, and `nptc.catalogue.designations.add_synonyms` deliberately does no
 * splitting of its own (`backend/tests/test_catalogue_designations.py` calls
 * `split_synonyms` as the caller's job). So the pasted-cell case FR-04 exists
 * for - a decade of `Zovirax;;Cyclir` in one spreadsheet box - has to be split
 * on this side of the wire.
 */

// The delimiter the legacy `RCPA Synonyms` column actually uses.
const SYNONYM_DELIMITER = ";";
// Comma-*space*, not a bare comma, matching the Python exactly: a term is
// allowed to contain a comma ("Smith, factor V") and a bare-comma fallback
// would tear one in half. `"a,b"` is therefore one term, not two.
const SYNONYM_FALLBACK_DELIMITER = ", ";

/**
 * Exactly the characters Python's `str.strip()` removes - the set `str.isspace()`
 * is true for.
 *
 * `String.prototype.trim()` is *not* that set, and the difference is entirely
 * inside PRD Appendix A.1's own subject matter, which is the one place this
 * mirror could not afford to drift (ADR-0029, review finding 5). Python strips
 * U+0085 and U+001C-U+001F; JavaScript does not. JavaScript trims U+FEFF;
 * Python does not - so the platform default would silently repair a
 * zero-width no-break space that the catalogue is meant to refuse, and leave a
 * NEL that the transform would have removed. Spelling the set out keeps both
 * sides answering the same question, and the shared fixtures in
 * `split-synonyms.test.ts` name the codepoints that used to separate them.
 */
const PYTHON_SPACE_CLASS =
  "[\t\n\v\f\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a" +
  "\u2028\u2029\u202f\u205f\u3000]";
// Written as escapes, never as the characters themselves: a source file that
// contains an invisible character is the defect class this platform exists to
// eliminate.
const PYTHON_WHITESPACE = new RegExp(
  `^${PYTHON_SPACE_CLASS}+|${PYTHON_SPACE_CLASS}+$`,
  "gu",
);

/** `str.strip()`, not `trim()`. See `PYTHON_WHITESPACE`. */
function strip(part: string): string {
  return part.replace(PYTHON_WHITESPACE, "");
}

/**
 * The individual terms a pasted synonym cell holds, in the order given.
 *
 * Empty parts are dropped rather than reported: a doubled delimiter
 * (`"Zovirax;;Cyclir"`) or a trailing one (`"Aciclovir ; Acyclovir ;  "`) is
 * the sample's own defect, and FR-04's requirement is that the empty row it
 * would produce is unrepresentable. The screen makes the drop visible by
 * showing the caller what will be created, rather than by a second function
 * reporting that something was discarded.
 */
export function splitSynonyms(text: string): string[] {
  const delimiter = text.includes(SYNONYM_DELIMITER)
    ? SYNONYM_DELIMITER
    : SYNONYM_FALLBACK_DELIMITER;
  return text
    .split(delimiter)
    .map((part) => strip(part))
    .filter((part) => part.length > 0);
}
