import { describe, expect, it } from "vitest";

import {
  auditFilterQuery,
  filterValues,
  formatEventTime,
  isCalendarDate,
  isUuid,
  startOfNextDay,
  validateAuditFilters,
} from "./audit-filters.ts";

const ACTOR = "3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8";

describe("isUuid", () => {
  it("accepts a user id in either case", () => {
    expect(isUuid(ACTOR)).toBe(true);
    expect(isUuid(ACTOR.toUpperCase())).toBe(true);
  });

  it("refuses text that is not a user id", () => {
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid(`${ACTOR}0`)).toBe(false);
    expect(isUuid("")).toBe(false);
  });
});

describe("isCalendarDate", () => {
  it("accepts a real day, including a leap day", () => {
    expect(isCalendarDate("2026-10-07")).toBe(true);
    expect(isCalendarDate("2028-02-29")).toBe(true);
  });

  it("refuses a day that does not exist or is not in YYYY-MM-DD form", () => {
    expect(isCalendarDate("2026-02-30")).toBe(false);
    expect(isCalendarDate("2027-02-29")).toBe(false);
    expect(isCalendarDate("2026-13-01")).toBe(false);
    expect(isCalendarDate("07/10/2026")).toBe(false);
  });
});

describe("startOfNextDay", () => {
  it("rolls over a month and a year", () => {
    expect(startOfNextDay("2026-10-31")).toBe("2026-11-01T00:00:00+10:00");
    expect(startOfNextDay("2026-12-31")).toBe("2027-01-01T00:00:00+10:00");
    expect(startOfNextDay("2028-02-28")).toBe("2028-02-29T00:00:00+10:00");
  });
});

describe("validateAuditFilters", () => {
  it("accepts no filters and a complete valid set", () => {
    expect(validateAuditFilters({})).toEqual([]);
    expect(
      validateAuditFilters({
        actor: ACTOR,
        entity_type: "catalogue_entry",
        entity_id: "NPTC-000001",
        action: "catalogue_entry.updated",
        from: "2026-10-01",
        to: "2026-10-07",
      }),
    ).toEqual([]);
  });

  it("refuses an actor that is not a user id", () => {
    expect(validateAuditFilters({ actor: "alice" })).toEqual([
      expect.objectContaining({ key: "actor" }),
    ]);
  });

  it("refuses an entity id without an entity type", () => {
    expect(validateAuditFilters({ entity_id: "NPTC-000001" })).toEqual([
      expect.objectContaining({ key: "entity_id" }),
    ]);
    expect(validateAuditFilters({ entity_id: "  ", entity_type: "" })).toEqual([]);
  });

  it("refuses a date that is not a day", () => {
    const errors = validateAuditFilters({ from: "2026-02-30", to: "tomorrow" });
    expect(errors.map((error) => error.key)).toEqual(["from", "to"]);
  });

  it("allows a range of one day and refuses a backwards range", () => {
    expect(validateAuditFilters({ from: "2026-10-07", to: "2026-10-07" })).toEqual([]);
    expect(validateAuditFilters({ from: "2026-10-08", to: "2026-10-07" })).toEqual([
      expect.objectContaining({ key: "to" }),
    ]);
  });
});

describe("filterValues", () => {
  it("trims values and drops blank ones", () => {
    expect(filterValues({ action: "  a.b ", entity_type: "   ", actor: "" })).toEqual({
      action: "a.b",
    });
  });
});

describe("auditFilterQuery", () => {
  it("sends nothing for no filters", () => {
    expect(auditFilterQuery({})).toEqual({
      actor_user_id: undefined,
      entity_type: undefined,
      entity_id: undefined,
      action: undefined,
      occurred_from: undefined,
      occurred_to: undefined,
    });
  });

  it("names the actor by user id and bounds the range at +10:00, the To day inclusive", () => {
    expect(
      auditFilterQuery({
        actor: ACTOR,
        entity_type: "catalogue_entry",
        entity_id: "NPTC-000001",
        action: "catalogue_entry.updated",
        from: "2026-10-01",
        to: "2026-10-07",
      }),
    ).toEqual({
      actor_user_id: ACTOR,
      entity_type: "catalogue_entry",
      entity_id: "NPTC-000001",
      action: "catalogue_entry.updated",
      occurred_from: "2026-10-01T00:00:00+10:00",
      occurred_to: "2026-10-08T00:00:00+10:00",
    });
  });
});

describe("formatEventTime", () => {
  it("shows the time at UTC+10 whatever the machine's zone", () => {
    expect(formatEventTime("2026-10-07T20:30:15+00:00")).toBe("8 Oct 2026, 06:30:15");
  });

  it("returns an unreadable value as given", () => {
    expect(formatEventTime("soon")).toBe("soon");
  });
});
