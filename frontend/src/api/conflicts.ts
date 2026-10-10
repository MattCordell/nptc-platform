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
export type PropertyValidationBody = components["schemas"]["PropertyValidationResponse"];
export type PropertyIssue = components["schemas"]["PropertyIssueItem"];

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

/**
 * The FR-09/FR-10/FR-88/FR-89 field-level validation body, or `null` if this
 * refusal is not one (issue #151, #248's `PropertyValidationResponse`).
 *
 * Status 422, not 409 - a different code from the two conflict bodies above,
 * so this checks `error.status` directly rather than sharing `conflictBody`.
 * Keyed on `issues` being an array: FastAPI's own `HTTPValidationError` (a
 * malformed request body that never reached the route) is also a 422 with a
 * `detail` array, but under the key `detail`, not `issues` - so the two
 * shapes cannot be confused even though both arrive as this status.
 */
export function asPropertyValidationError(error: unknown): PropertyValidationBody | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  const body = error.body as Record<string, unknown>;
  return Array.isArray(body.issues) ? (body as unknown as PropertyValidationBody) : null;
}

export type DuplicatesBody = components["schemas"]["SubmissionDuplicatesResponse"];
export type DuplicateMatch = components["schemas"]["DuplicateMatchItem"];
export type QuotaBody = components["schemas"]["SubmissionQuotaResponse"];
export type SubmissionFieldRefusal =
  components["schemas"]["SubmissionFieldRefusalResponse"];
export type SubmissionField = SubmissionFieldRefusal["field"];

/**
 * A `Record`, so a field the server starts to name is a type error here until
 * this list knows it, not a refusal the form silently drops.
 */
const SUBMISSION_FIELDS: Record<SubmissionField, true> = {
  preferred_term: true,
  synonyms: true,
  snomed_code: true,
  reference_url: true,
  notes: true,
  organisation: true,
};

const QUOTA_LIMITS: Record<QuotaBody["limit"], true> = {
  hourly: true,
  lifetime: true,
  concurrent: true,
};

function bodyOf(
  error: unknown,
  statuses: readonly number[],
): Record<string, unknown> | null {
  if (!(error instanceof ApiError) || !statuses.includes(error.status)) {
    return null;
  }
  if (typeof error.body !== "object" || error.body === null) {
    return null;
  }
  return error.body as Record<string, unknown>;
}

/**
 * A 422, 502 or 503 from a submission route that names the request field it
 * concerns, or `null` if this refusal is anything else.
 *
 * Keyed on `field`, never on the wording of `detail`: the sentence is for
 * people and the server may reword it. A status alone is not enough, because
 * other routes send 422, 502 and 503 with no field.
 */
export function asFieldRefusal(error: unknown): SubmissionFieldRefusal | null {
  const body = bodyOf(error, [422, 502, 503]);
  if (
    body === null ||
    typeof body.detail !== "string" ||
    typeof body.field !== "string" ||
    !Object.hasOwn(SUBMISSION_FIELDS, body.field)
  ) {
    return null;
  }
  return body as unknown as SubmissionFieldRefusal;
}

/**
 * The FR-25 409 that lists the matches a submitter must confirm, or `null` if
 * this refusal is anything else. Keyed on `matches` being an array: `detail`
 * is on every 409.
 */
export function asDuplicateMatches(error: unknown): DuplicatesBody | null {
  const body = bodyOf(error, [409]);
  if (body === null || !Array.isArray(body.matches)) {
    return null;
  }
  return body as unknown as DuplicatesBody;
}

/**
 * The FR-43 429 for a used-up submission quota, or `null` if this refusal is
 * anything else, such as the request-budget 429, which has no `limit`.
 */
export function asQuotaRefusal(error: unknown): QuotaBody | null {
  const body = bodyOf(error, [429]);
  if (
    body === null ||
    typeof body.limit !== "string" ||
    !Object.hasOwn(QUOTA_LIMITS, body.limit) ||
    typeof body.maximum !== "number"
  ) {
    return null;
  }
  return body as unknown as QuotaBody;
}

/**
 * The whole seconds a refusal's `Retry-After` header asks the caller to wait,
 * or `null` if there is none or it is not a positive whole number. The server
 * sends none for a lifetime quota, because waiting does not lift it.
 */
export function retryAfterSeconds(error: unknown): number | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  const raw = error.headers.get("Retry-After");
  if (raw === null || !/^\d+$/.test(raw.trim())) {
    return null;
  }
  const seconds = Number(raw.trim());
  return seconds >= 1 ? seconds : null;
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
