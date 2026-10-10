/**
 * Server limits this screen has to know before it sends a request.
 *
 * Its own module, not a constant inside a component file, for two
 * reasons: a `.tsx` file that exports a non-component trips
 * `react-refresh/only-export-components`, and a copied server constant needs a
 * test that reads the server's own document - which needs somewhere to import
 * it from.
 */

/**
 * `_MAX_TERMS_PER_BATCH` in `nptc.api.routers.catalogue_designations`.
 *
 * A copy, because a list's `max_length` does not survive into `schema.ts` -
 * `openapi-typescript` has nowhere to put it in a TypeScript type. It is
 * checked against `docs/api/openapi.json`'s `maxItems` in `limits.test.ts`,
 * which the `openapi-document-is-current` hook keeps in step with the router,
 * so this fails on the day the server moves rather than drifting quietly.
 *
 * The check it feeds is about *wording*, never authority: the server still
 * refuses an oversized batch. What the browser adds is a message naming the
 * count, beside the preview that produced it, instead of the bare 422 that
 * FastAPI's `ValidationError` array gives `refusalDetail` nothing to say
 * about.
 */
export const MAX_TERMS_PER_BATCH = 100;

/**
 * The size bounds of `POST /submissions`, copied for the same reason and
 * checked the same way (`limits.test.ts`). The form stops typing at the bound
 * where the input can, and names the bound where it cannot, instead of showing
 * the bare 422 that FastAPI gives for a request over its limits.
 */
export const SUBMISSION_LIMITS = {
  /** `preferred_term` and each synonym. */
  termLength: 500,
  synonyms: 100,
  referenceUrlLength: 2048,
  notesLength: 10000,
  organisationLength: 500,
  /** Values one property may carry. */
  valuesPerProperty: 25,
  justificationLength: 2000,
} as const;
