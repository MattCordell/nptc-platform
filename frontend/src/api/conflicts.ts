import type { components } from "./schema.ts";
import { ApiError } from "./unwrap.ts";

/**
 * Narrowing the two 409 bodies that carry a payload (issues #224, #227).
 *
 * Most refusals in this API are `ErrorResponse { detail }` and belong in
 * `Form`'s `formError` as a sentence (ADR-0026). Two are not, because a
 * sentence would withhold exactly what the requirement exists to deliver:
 *
 * - **FR-05 / PRD §17.2 item 5** - an error-severity collision names the
 *   colliding entry, so the editor can go and look at it rather than being
 *   shown a status code.
 * - **FR-38** - a stale `expected_row_version` names the conflicting values,
 *   so the editor can reconcile rather than retry blind.
 *
 * Both are declared response models on the routes that can emit them, so
 * `schema.ts` types them and this module never invents a shape. What it does
 * do is decide *which* member of an `anyOf` a given body is: the amendment
 * route's 409 is a union of three, and `ApiError.body` is `unknown` by
 * construction (`unwrap.ts`).
 *
 * Each guard checks the status **and** the discriminating property. Status
 * alone is not enough - a plain `{detail}` 409 (a duplicate active term, an
 * already-retired designation) reaches the same catch and must narrow to
 * `null` so the caller falls back to the sentence.
 */

export type CollisionBody = components["schemas"]["DesignationCollisionResponse"];
export type VersionConflictBody = components["schemas"]["VersionConflictResponse"];
export type CollisionItem = components["schemas"]["CollisionItem"];
export type FieldConflict = components["schemas"]["FieldConflictItem"];

function conflictBody(error: unknown): Record<string, unknown> | null {
  // `instanceof ApiError` rather than a duck-typed `status` check: every
  // failed call in this app goes through `unwrap`, so anything else reaching
  // here is a bug worth letting fall through to the generic message rather
  // than a shape worth guessing at.
  if (!(error instanceof ApiError) || error.status !== 409) {
    return null;
  }
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  return error.body as Record<string, unknown>;
}

/**
 * The FR-05 collision payload, or `null` if this refusal is not one.
 *
 * Keyed on `collisions` being an array: `detail` is present on every 409 and
 * discriminates nothing.
 */
export function asCollisionError(error: unknown): CollisionBody | null {
  const body = conflictBody(error);
  if (body === null || !Array.isArray(body.collisions)) {
    return null;
  }
  return body as unknown as CollisionBody;
}

/**
 * The FR-38 version-conflict payload, or `null` if this refusal is not one.
 *
 * Keyed on `current_row_version` being a number rather than on `conflicts`
 * being an array. `conflicts` is legitimately **empty** whenever the
 * concurrent edit touched a different field from this one - the entry moved,
 * so the save is still refused, but there is no field-level disagreement to
 * report (`nptc.catalogue.errors.ConflictReport`). Discriminating on it would
 * mistake that ordinary case for a body of another shape.
 */
export function asVersionConflict(error: unknown): VersionConflictBody | null {
  const body = conflictBody(error);
  if (body === null || typeof body.current_row_version !== "number") {
    return null;
  }
  return body as unknown as VersionConflictBody;
}

/** The `detail` sentence any refusal carries, or `null` if it has none. */
export function refusalDetail(error: unknown): string | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  const detail = (error.body as Record<string, unknown>).detail;
  // A 422 from FastAPI's own validation carries `detail` as an *array* of
  // ValidationError, not a string (`HTTPValidationError`). Rendering that
  // would put `[object Object]` in front of an editor, so it is refused here
  // and the caller falls back to its own wording.
  return typeof detail === "string" ? detail : null;
}
