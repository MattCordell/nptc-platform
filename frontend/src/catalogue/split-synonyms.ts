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
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}
