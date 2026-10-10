import { describe, expect, it } from "vitest";

import { MAX_TERMS_PER_BATCH, SUBMISSION_LIMITS } from "./limits.ts";
import openapi from "../../../docs/api/openapi.json";

/**
 * The drift guard for a hand-copied server constant.
 *
 * `maxItems` does not reach `frontend/src/api/schema.ts` - `openapi-typescript`
 * has nowhere to put a list length in a TypeScript type - but it does reach
 * `docs/api/openapi.json`, which the `openapi-document-is-current` pre-commit
 * hook regenerates from the router. So the day `_MAX_TERMS_PER_BATCH` moves,
 * this fails, in the same spirit as exporting `adminEntryDetailKey` so a test
 * asserts against the real key rather than a retyped one.
 *
 * Importing the document is what `resolveJsonModule` is on for in
 * `tsconfig.app.json` (the frontend has no `@types/node`, so reading it with
 * `fs` would not typecheck). It is imported only from this test, so nothing
 * ships in the bundle.
 */
describe("MAX_TERMS_PER_BATCH", () => {
  it("matches the limit the server's own OpenAPI document declares", () => {
    const terms = openapi.components.schemas.AddDesignationsRequest.properties.terms;

    // Asserted, not optional-chained into a pass: a document that stops
    // declaring the cap is itself the drift this test exists to catch.
    expect(terms.maxItems).toBe(MAX_TERMS_PER_BATCH);
  });
});

describe("SUBMISSION_LIMITS", () => {
  const { CreateSubmissionRequest: create, SubmissionPropertyValueRequest: value } =
    openapi.components.schemas;
  const properties = create.properties;

  it("matches every bound the create body's OpenAPI schema declares", () => {
    expect(properties.preferred_term.maxLength).toBe(SUBMISSION_LIMITS.termLength);
    expect(properties.synonyms.items.maxLength).toBe(SUBMISSION_LIMITS.termLength);
    expect(properties.synonyms.maxItems).toBe(SUBMISSION_LIMITS.synonyms);
    expect(properties.reference_url.maxLength).toBe(SUBMISSION_LIMITS.referenceUrlLength);
    expect(properties.notes.anyOf[0].maxLength).toBe(SUBMISSION_LIMITS.notesLength);
    expect(properties.organisation.anyOf[0].maxLength).toBe(
      SUBMISSION_LIMITS.organisationLength,
    );
    expect(properties.property_values.additionalProperties.maxItems).toBe(
      SUBMISSION_LIMITS.valuesPerProperty,
    );
    expect(value.properties.justification.anyOf[0].maxLength).toBe(
      SUBMISSION_LIMITS.justificationLength,
    );
  });
});
