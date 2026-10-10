import { describe, expect, it } from "vitest";

import {
  asCollisionError,
  asDuplicateMatches,
  asFieldRefusal,
  asQuotaRefusal,
  asVersionConflict,
  refusalDetail,
  retryAfterSeconds,
} from "./conflicts.ts";
import { ApiError } from "./unwrap.ts";

/**
 * The amendment route's 409 is an `anyOf` of three bodies, so "status 409" is
 * not enough to know what a refusal is carrying. These guards are what stands
 * between the screen and a cast.
 */

const COLLISION = {
  detail: "This term matches another entry's preferred term or synonym.",
  collisions: [
    { severity: "error", business_key: "NPTC-000247", preferred_term: "Adrenal Ab" },
  ],
};

const VERSION_CONFLICT = {
  detail: "This entry was changed by someone else since you loaded it.",
  business_key: "NPTC-000247",
  expected_row_version: 3,
  current_row_version: 4,
  conflicts: [
    { field: "preferred_term", submitted: "Ferritin", current: "Serum ferritin" },
  ],
  changed_by: "A Curator",
  changed_at: "2026-09-02T00:00:00Z",
};

describe("asCollisionError", () => {
  it("narrows a 409 carrying collisions and keeps the named entry", () => {
    const body = asCollisionError(new ApiError(409, COLLISION));

    expect(body?.collisions).toHaveLength(1);
    expect(body?.collisions[0]?.business_key).toBe("NPTC-000247");
    expect(body?.collisions[0]?.preferred_term).toBe("Adrenal Ab");
  });

  // The principal failure mode: most 409s on these routes are a plain
  // `{detail}` - a duplicate active term, a designation already retired - and
  // they reach the same catch. Narrowing one of those to a collision would put
  // an empty "collides with:" list in front of an editor.
  it("returns null for a plain {detail} 409", () => {
    expect(
      asCollisionError(new ApiError(409, { detail: "Already retired." })),
    ).toBeNull();
  });

  it("returns null for a version conflict, which is also a 409", () => {
    expect(asCollisionError(new ApiError(409, VERSION_CONFLICT))).toBeNull();
  });

  it("returns null for a non-409, an empty body, and a non-ApiError", () => {
    expect(asCollisionError(new ApiError(422, COLLISION))).toBeNull();
    expect(asCollisionError(new ApiError(409, null))).toBeNull();
    expect(asCollisionError(new ApiError(409, "not json"))).toBeNull();
    expect(asCollisionError(new Error("network down"))).toBeNull();
  });
});

describe("asVersionConflict", () => {
  it("narrows a 409 carrying a row version and keeps the conflicting values", () => {
    const body = asVersionConflict(new ApiError(409, VERSION_CONFLICT));

    expect(body?.current_row_version).toBe(4);
    expect(body?.changed_by).toBe("A Curator");
    expect(body?.conflicts[0]?.field).toBe("preferred_term");
    expect(body?.conflicts[0]?.current).toBe("Serum ferritin");
  });

  // `conflicts` is empty whenever the concurrent edit touched a *different*
  // field: the entry moved so the save is still refused, but there is no
  // field-level disagreement to report. Discriminating on `conflicts` would
  // have mistaken this ordinary case for a body of another shape - which is
  // why the guard keys on `current_row_version` instead.
  it("narrows a conflict whose conflicts list is empty", () => {
    const body = asVersionConflict(
      new ApiError(409, { ...VERSION_CONFLICT, conflicts: [] }),
    );

    expect(body).not.toBeNull();
    expect(body?.conflicts).toEqual([]);
    expect(body?.current_row_version).toBe(4);
  });

  it("returns null for a collision and for a plain {detail} 409", () => {
    expect(asVersionConflict(new ApiError(409, COLLISION))).toBeNull();
    expect(
      asVersionConflict(new ApiError(409, { detail: "Already retired." })),
    ).toBeNull();
  });
});

describe("refusalDetail", () => {
  it("returns the sentence a refusal carries", () => {
    expect(
      refusalDetail(new ApiError(403, { detail: "You do not have permission." })),
    ).toBe("You do not have permission.");
  });

  // FastAPI's own validation failures carry `detail` as an array of
  // ValidationError, not a string. Rendering that would put "[object Object]"
  // in front of an editor, so the caller has to fall back to its own wording.
  it("returns null when detail is FastAPI's ValidationError array", () => {
    const body = {
      detail: [{ loc: ["body", "terms"], msg: "too short", type: "value_error" }],
    };

    expect(refusalDetail(new ApiError(422, body))).toBeNull();
  });

  it("returns null for an empty body and for a non-ApiError", () => {
    expect(refusalDetail(new ApiError(500, null))).toBeNull();
    expect(refusalDetail(new Error("network down"))).toBeNull();
  });
});

describe("asFieldRefusal", () => {
  it.each([422, 503])("narrows a %s that names a submission field", (status) => {
    const body = {
      detail: "The reference link could not be reached.",
      field: "reference_url",
    };

    expect(asFieldRefusal(new ApiError(status, body))).toEqual(body);
  });

  // The principal failure mode: a 422 or 503 from any other route has no
  // `field`, and must fall back to the sentence rather than mark a field.
  it("is null for a 422 or 503 with no field", () => {
    expect(asFieldRefusal(new ApiError(422, { detail: "Not valid." }))).toBeNull();
    expect(asFieldRefusal(new ApiError(503, { detail: "Try again." }))).toBeNull();
  });

  it("is null for a field the form does not have, and for FastAPI's array detail", () => {
    expect(asFieldRefusal(new ApiError(422, { detail: "x", field: "kind" }))).toBeNull();
    expect(asFieldRefusal(new ApiError(422, { detail: [{ msg: "x" }] }))).toBeNull();
  });

  it("is null for another status, and for an error that is not an ApiError", () => {
    expect(
      asFieldRefusal(new ApiError(409, { detail: "x", field: "snomed_code" })),
    ).toBeNull();
    expect(asFieldRefusal(new Error("offline"))).toBeNull();
  });

  it("does not take a prototype property for a field", () => {
    expect(
      asFieldRefusal(new ApiError(422, { detail: "x", field: "toString" })),
    ).toBeNull();
  });
});

describe("asDuplicateMatches", () => {
  const MATCHES = {
    detail: "This submission may duplicate an entry.",
    matches: [{ source: "catalogue_entry", key: "NPTC-000247" }],
  };

  it("narrows a 409 carrying matches", () => {
    expect(asDuplicateMatches(new ApiError(409, MATCHES))?.matches).toHaveLength(1);
  });

  it("is null for a plain 409, and for matches on another status", () => {
    expect(asDuplicateMatches(new ApiError(409, { detail: "Conflict." }))).toBeNull();
    expect(asDuplicateMatches(new ApiError(422, MATCHES))).toBeNull();
  });
});

describe("asQuotaRefusal", () => {
  it.each(["hourly", "lifetime", "concurrent"])("narrows a %s quota refusal", (limit) => {
    const body = { detail: "Quota used.", limit, maximum: 5 };

    expect(asQuotaRefusal(new ApiError(429, body))).toEqual(body);
  });

  // The request-budget 429 shares the status and carries no `limit`.
  it("is null for the request-budget 429, and for a limit it does not know", () => {
    expect(asQuotaRefusal(new ApiError(429, { detail: "Slow down." }))).toBeNull();
    expect(
      asQuotaRefusal(new ApiError(429, { detail: "x", limit: "daily", maximum: 5 })),
    ).toBeNull();
    expect(
      asQuotaRefusal(new ApiError(429, { detail: "x", limit: "hourly", maximum: "5" })),
    ).toBeNull();
  });
});

describe("retryAfterSeconds", () => {
  function refused(retryAfter?: string) {
    const headers = new Headers();
    if (retryAfter !== undefined) {
      headers.set("Retry-After", retryAfter);
    }
    return new ApiError(429, {}, headers);
  }

  it("reads whole seconds", () => {
    expect(retryAfterSeconds(refused("1800"))).toBe(1800);
  });

  it("is null when there is no header, as for a lifetime quota", () => {
    expect(retryAfterSeconds(refused())).toBeNull();
  });

  it("is null for a value that is not a positive whole number", () => {
    for (const value of ["0", "-5", "soon", "1.5", "Wed, 21 Oct 2026 07:28:00 GMT", ""]) {
      expect(retryAfterSeconds(refused(value))).toBeNull();
    }
  });

  it("is null for an error that is not an ApiError", () => {
    expect(retryAfterSeconds(new Error("offline"))).toBeNull();
  });
});
