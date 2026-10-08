const SPECIMEN_SUFFIX = /\s+specimen$/i;

/**
 * `term` without a trailing "specimen" word, so "Serum specimen" shows as
 * "Serum". A bare "Specimen" is returned as it is, never empty.
 *
 * The frontend twin of `trim_specimen_suffix` in
 * `backend/src/nptc/exports/semantic_tag.py`: change one, change both.
 */
export function trimSpecimenSuffix(term: string): string {
  const trimmed = term.replace(SPECIMEN_SUFFIX, "");
  return trimmed === "" ? term : trimmed;
}
