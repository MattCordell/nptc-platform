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
  it("turns the action name into a sentence", () => {
    expect(describeChange(event({ action: "catalogue_entry.updated" })).action).toBe(
      "Catalogue entry updated",
    );
    expect(
      describeChange(event({ action: "code_binding.replacement_linked" })).action,
    ).toBe("Code binding replacement linked");
  });

  it("names the changed fields in words", () => {
    expect(
      describeChange(event({ changed_fields: ["preferred_term", "status"] })).fields,
    ).toBe("Preferred term, Status");
  });

  it("spells out a field name that would otherwise be an unexplained acronym", () => {
    expect(
      describeChange(event({ changed_fields: ["code", "fsn", "au_preferred_term"] }))
        .fields,
    ).toBe("Code, Fully specified name, AU preferred term");
  });

  it("leaves out bookkeeping columns and the internal entry key", () => {
    expect(
      describeChange(
        event({
          changed_fields: ["entry_id", "status", "row_version", "updated_at", "id"],
        }),
      ).fields,
    ).toBe("Status");
  });

  it("names no fields when none are left to name", () => {
    expect(describeChange(event({ changed_fields: [] })).fields).toBeNull();
    expect(
      describeChange(event({ changed_fields: ["row_version", "updated_at"] })).fields,
    ).toBeNull();
  });

  it("copes with an action that has no dot", () => {
    expect(describeChange(event({ action: "imported" })).action).toBe("Imported");
  });
});
