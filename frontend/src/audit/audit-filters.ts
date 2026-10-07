import type { AuditSearch } from "../router/search-params.ts";

/**
 * The audit log screen's filter rules (NFR-12). Pure functions, so the page
 * and its tests share one definition of what a valid filter is and what the
 * API is sent.
 *
 * Days are Australian Eastern Standard Time, a fixed UTC+10 with no daylight
 * saving. The API needs an offset on every timestamp, and a date input yields
 * a bare date, so the screen supplies the offset.
 */

const OFFSET = "+10:00";
const OFFSET_HOURS = 10;

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

export const AUDIT_FIELD_IDS = {
  actor: "audit-actor",
  entity_type: "audit-entity-type",
  entity_id: "audit-entity-id",
  action: "audit-action",
  from: "audit-from",
  to: "audit-to",
} as const;

export type AuditFilterKey = keyof typeof AUDIT_FIELD_IDS;

export type AuditFilterValues = Partial<Record<AuditFilterKey, string>>;

export type AuditFilterError = { key: AuditFilterKey; message: string };

export const AUDIT_FILTER_KEYS = Object.keys(AUDIT_FIELD_IDS) as AuditFilterKey[];

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

/** A real calendar date in `YYYY-MM-DD` form; `2026-02-30` is not one. */
export function isCalendarDate(value: string): boolean {
  const match = DATE_PATTERN.exec(value);
  if (match === null) {
    return false;
  }
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function nextDay(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

/** The first instant of `date` in Australian Eastern Standard Time. */
export function startOfDay(date: string): string {
  return `${date}T00:00:00${OFFSET}`;
}

/**
 * The exclusive upper bound for an inclusive last day: the first instant of the
 * day after. The API's range is half open, so this makes the To date inclusive.
 */
export function startOfNextDay(date: string): string {
  return startOfDay(nextDay(date));
}

/** Every filter the validator reads from a URL search or a form draft. */
export function filterValues(source: AuditSearch | AuditFilterValues): AuditFilterValues {
  const values: AuditFilterValues = {};
  for (const key of AUDIT_FILTER_KEYS) {
    const value = source[key]?.trim();
    if (value) {
      values[key] = value;
    }
  }
  return values;
}

export function validateAuditFilters(source: AuditFilterValues): AuditFilterError[] {
  const values = filterValues(source);
  const errors: AuditFilterError[] = [];

  if (values.actor !== undefined && !isUuid(values.actor)) {
    errors.push({
      key: "actor",
      message:
        "Enter the actor as a user id, such as 3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8.",
    });
  }
  if (values.entity_id !== undefined && values.entity_type === undefined) {
    errors.push({
      key: "entity_id",
      message: "Enter an entity type as well, because an entity id needs one.",
    });
  }
  if (values.from !== undefined && !isCalendarDate(values.from)) {
    errors.push({ key: "from", message: "Enter From as a date, such as 2026-10-07." });
  }
  if (values.to !== undefined && !isCalendarDate(values.to)) {
    errors.push({ key: "to", message: "Enter To as a date, such as 2026-10-07." });
  }
  if (
    values.from !== undefined &&
    values.to !== undefined &&
    isCalendarDate(values.from) &&
    isCalendarDate(values.to) &&
    values.from > values.to
  ) {
    errors.push({ key: "to", message: "To must be the same day as From or later." });
  }
  return errors;
}

/**
 * The query the audit endpoints take. Both the list and the export read it;
 * the export has no `limit` or `before`, so those are added by the list alone.
 * Call only with filters that passed `validateAuditFilters`.
 */
export function auditFilterQuery(source: AuditFilterValues) {
  const values = filterValues(source);
  return {
    actor_user_id: values.actor,
    entity_type: values.entity_type,
    entity_id: values.entity_id,
    action: values.action,
    occurred_from: values.from === undefined ? undefined : startOfDay(values.from),
    occurred_to: values.to === undefined ? undefined : startOfNextDay(values.to),
  };
}

const TIME_FORMAT = new Intl.DateTimeFormat("en-AU", {
  timeZone: `Etc/GMT-${OFFSET_HOURS}`,
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** An event time in the same fixed UTC+10 the date filters use. */
export function formatEventTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : TIME_FORMAT.format(date);
}

export const FILTER_LABELS: Record<AuditFilterKey, string> = {
  actor: "Actor",
  entity_type: "Entity type",
  entity_id: "Entity id",
  action: "Action",
  from: "From",
  to: "To",
};
