import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/unwrap.ts";
import { endsRun, failureMessage, runSave } from "./run-save.ts";
import type { FieldChange, SendChange } from "./run-save.ts";

/**
 * The ordering, version-carrying and stop-or-continue rules of the edit form's
 * save run (FR-36, FR-38), with no React in the way.
 */

const TERM: FieldChange = {
  kind: "preferred_term",
  id: "preferred_term",
  label: "RCPA Preferred",
  currentTerm: "Ferritin",
  newTerm: "Serum ferritin",
};
const DISCIPLINE: FieldChange = {
  kind: "property",
  id: "property:discipline",
  label: "Discipline",
  key: "discipline",
  values: [{ value: "CH", justification: null }],
};
const USAGE: FieldChange = {
  kind: "property",
  id: "property:usage_guidance",
  label: "Usage guidance",
  key: "usage_guidance",
  values: [{ value: "Fasting sample.", justification: null }],
};

const versionConflict = new ApiError(409, {
  detail: "This entry changed after you opened it.",
  business_key: "NPTC-000006",
  expected_row_version: 3,
  current_row_version: 5,
  conflicts: [],
  changed_by: null,
  changed_at: null,
});
const propertyRefusal = new ApiError(422, {
  detail: "Some values are not valid.",
  issues: [
    {
      property_key: "discipline",
      label: "Discipline",
      code: "not_in_value_set",
      message: "XX is not an allowed Discipline.",
      ordinal: 0,
    },
  ],
});

describe("runSave", () => {
  it("sends each change in order, carrying the new row version into the next", async () => {
    const send = vi.fn<SendChange>(async (_change, version) => ({
      rowVersion: version + 1,
    }));

    const run = await runSave([TERM, DISCIPLINE, USAGE], send, 3);

    expect(send.mock.calls.map(([change, version]) => [change.id, version])).toEqual([
      ["preferred_term", 3],
      ["property:discipline", 4],
      ["property:usage_guidance", 5],
    ]);
    expect(run.rowVersion).toBe(6);
    expect(run.stopped).toBe(false);
    expect(run.outcomes.map((outcome) => outcome.status)).toEqual([
      "saved",
      "saved",
      "saved",
    ]);
  });

  it("records the server's length and stored term for a saved preferred term", async () => {
    const run = await runSave(
      [TERM],
      async () => ({ rowVersion: 4, length: 14, savedTerm: "Serum ferritin" }),
      3,
    );

    expect(run.outcomes[0]).toMatchObject({
      status: "saved",
      length: 14,
      savedTerm: "Serum ferritin",
    });
  });

  it("marks a refused field with its reason and still sends the rest", async () => {
    const send = vi.fn<SendChange>(async (change, version) => {
      if (change.id === "property:discipline") {
        throw propertyRefusal;
      }
      return { rowVersion: version + 1 };
    });

    const run = await runSave([TERM, DISCIPLINE, USAGE], send, 3);

    expect(run.outcomes.map((outcome) => outcome.status)).toEqual([
      "saved",
      "refused",
      "saved",
    ]);
    expect(run.outcomes[1]).toMatchObject({ message: "XX is not an allowed Discipline." });
    // The refusal changed nothing, so the next request carries the version after the first save.
    expect(send.mock.calls[2][1]).toBe(4);
    expect(run.rowVersion).toBe(5);
    expect(run.stopped).toBe(false);
  });

  it("treats a duplicate-term collision as a refusal of that field", async () => {
    const collision = new ApiError(409, {
      detail: "Term in use.",
      collisions: [
        {
          severity: "error",
          business_key: "NPTC-000042",
          preferred_term: "Serum ferritin",
          label_provenance: {},
        },
      ],
    });
    const run = await runSave(
      [TERM, DISCIPLINE],
      async (change, version) => {
        if (change.kind === "preferred_term") {
          throw collision;
        }
        return { rowVersion: version + 1 };
      },
      3,
    );

    expect(run.outcomes.map((outcome) => outcome.status)).toEqual(["refused", "saved"]);
    expect(run.outcomes[0]).toMatchObject({
      message: expect.stringContaining("NPTC-000042") as string,
    });
  });

  it("stops at a version conflict and sends nothing further", async () => {
    const send = vi.fn<SendChange>(async (change, version) => {
      if (change.id === "property:discipline") {
        throw versionConflict;
      }
      return { rowVersion: version + 1 };
    });

    const run = await runSave([TERM, DISCIPLINE, USAGE], send, 3);

    expect(send).toHaveBeenCalledTimes(2);
    expect(run.outcomes.map((outcome) => outcome.status)).toEqual([
      "saved",
      "failed",
      "not-sent",
    ]);
    expect(run.outcomes[2]).toMatchObject({
      message: expect.stringContaining("This entry changed after you opened it.") as string,
    });
    expect(run.stopped).toBe(true);
  });

  it.each([401, 403, 429, 500, 503])("stops the run on a %i", async (status) => {
    const send = vi.fn<SendChange>(async () => {
      throw new ApiError(status, { detail: "Refused." });
    });

    const run = await runSave([DISCIPLINE, USAGE], send, 3);

    expect(send).toHaveBeenCalledTimes(1);
    expect(run.outcomes.map((outcome) => outcome.status)).toEqual(["failed", "not-sent"]);
  });

  it("stops the run when the request never reaches the server", async () => {
    const send = vi.fn<SendChange>(async () => {
      throw new TypeError("Failed to fetch");
    });

    const run = await runSave([DISCIPLINE, USAGE], send, 3);

    expect(run.outcomes.map((outcome) => outcome.status)).toEqual(["failed", "not-sent"]);
    expect(run.outcomes[0]).toMatchObject({
      message: expect.stringContaining("could not be reached") as string,
    });
  });

  it("returns an empty run for no changes", async () => {
    const send = vi.fn<SendChange>();
    const run = await runSave([], send, 3);
    expect(send).not.toHaveBeenCalled();
    expect(run).toEqual({ outcomes: [], rowVersion: 3, stopped: false });
  });
});

describe("failureMessage", () => {
  it("never shows an HTTP status code", () => {
    for (const status of [401, 403, 404, 429, 500, 502]) {
      expect(failureMessage(new ApiError(status, undefined))).not.toMatch(/\b\d{3}\b/);
    }
  });

  it("prefers the server's own sentence", () => {
    expect(failureMessage(new ApiError(403, { detail: "Multi-factor sign-in needed." }))).toBe(
      "Multi-factor sign-in needed.",
    );
  });

  it("does not render a FastAPI validation array as text", () => {
    const message = failureMessage(
      new ApiError(422, { detail: [{ loc: ["body"], msg: "bad", type: "x" }] }),
    );
    expect(message).not.toContain("[object Object]");
  });
});

describe("endsRun", () => {
  it("does not end the run for a 409 that is not a version conflict", () => {
    expect(endsRun(new ApiError(409, { detail: "Already retired." }))).toBe(false);
  });

  it("ends the run for a 409 that is a version conflict", () => {
    expect(endsRun(versionConflict)).toBe(true);
  });
});
