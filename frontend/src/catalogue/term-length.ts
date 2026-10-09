import { normaliseForComparison } from "./python-text.ts";

/**
 * The character count of a preferred term, as the editor types it (FR-85).
 *
 * A deliberate, tested mirror of `preferred_term_length` in
 * `backend/src/nptc/catalogue/term_hygiene.py` - see
 * `docs/adr/0030-domain-logic-at-the-browser-boundary.md`. It counts code
 * points, as Python's `len` does, not UTF-16 units. The server's figure, shown
 * after a save, is the authority; this one only keeps the display live.
 * `shared/tests/fixtures/term-length-cases.json` is read by both test suites.
 */
export function termLength(term: string): number {
  return Array.from(normaliseForComparison(term)).length;
}
