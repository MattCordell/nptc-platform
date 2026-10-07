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

  // The field sets below are the audited columns of each model, as the history
  // route sends them (`__audit_fields__` in `backend/src/nptc/db/models`).
  it.each([
    [
      "a code binding created",
      "code_binding.created",
      ["entry_id", "system", "code", "fsn", "edition_hint", "status"],
      "System, Code, Fully specified name, Edition hint, Status",
    ],
    [
      "a binding replaced",
      "code_binding.replacement_linked",
      ["replaced_by_binding_id", "status", "retirement_reason"],
      "Status, Retirement reason",
    ],
    [
      "a property value set",
      "property_value.set",
      ["entry_id", "property_key", "ordinal", "value", "justification"],
      "Property, Value, Justification",
    ],
    [
      "a designation created",
      "designation.created",
      ["entry_id", "term", "use", "language", "status"],
      "Term, Use, Language, Status",
    ],
    [
      "a collision acknowledged",
      "designation_collision.acknowledged",
      ["entry_id", "term_key", "language", "reason", "acknowledged_by_user_id"],
      "Language, Reason",
    ],
  ])("names no internal key for %s", (_label, action, changed_fields, expected) => {
    const text = describeChange(event({ action, changed_fields }));

    expect(text.fields).toBe(expected);
    expect(text.fields).not.toMatch(/\bid\b|key\b|ordinal/i);
  });

  it("hides any audited key that ends in _id, including one added later", () => {
    expect(
      describeChange(event({ changed_fields: ["status", "future_thing_id"] })).fields,
    ).toBe("Status");
  });

  it("names no fields when none are left to name", () => {
    expect(describeChange(event({ changed_fields: [] })).fields).toBeNull();
    expect(
      describeChange(event({ changed_fields: ["entry_id", "ordinal"] })).fields,
    ).toBeNull();
  });

  it("copes with an action that has no dot", () => {
    expect(describeChange(event({ action: "imported" })).action).toBe("Imported");
  });
});
