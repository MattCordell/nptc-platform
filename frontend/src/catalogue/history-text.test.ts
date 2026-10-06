import { describe, expect, it } from "vitest";

import type { components } from "../api/schema.ts";
import { describeChange } from "./history-text.ts";

type HistoryEvent = components["schemas"]["HistoryEvent"];

function event(overrides: Partial<HistoryEvent>): HistoryEvent {
  return {
    occurred_at: "2026-09-01T12:00:00Z",
    action: "catalogue_entry.updated",
    changed_by: null,
    changed_fields: [],
    note: null,
    ...overrides,
  };
}

describe("describeChange", () => {
  it("names the changed fields in words", () => {
    expect(describeChange(event({ changed_fields: ["preferred_term", "status"] }))).toBe(
      "Changed: Preferred term, Status",
    );
  });

  it("leaves out bookkeeping columns that change on every write", () => {
    expect(
      describeChange(event({ changed_fields: ["status", "row_version", "updated_at"] })),
    ).toBe("Changed: Status");
  });

  it("falls back to the verb of the action when no field is left to name", () => {
    expect(describeChange(event({ changed_fields: ["row_version", "updated_at"] }))).toBe(
      "Updated",
    );
    expect(describeChange(event({ action: "designation.retired" }))).toBe("Retired");
  });

  it("never shows the internal action name", () => {
    expect(describeChange(event({ action: "code_binding.replacement_linked" }))).toBe(
      "Replacement linked",
    );
  });

  it("copes with an action that has no dot", () => {
    expect(describeChange(event({ action: "imported" }))).toBe("Imported");
  });
});
