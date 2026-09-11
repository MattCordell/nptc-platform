import type { StatusTone } from "../components/status-badge.tsx";

/**
 * The lifecycle values `statusToneFor` maps by name, matching
 * `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` (derived from the same
 * enum server-side). Hand-written, not derived from `STATUS_OPTIONS` via
 * `satisfies` (PR #327 second review): `satisfies` only checks a value's
 * *shape*, not its literal type, against a target property typed `string` -
 * `(typeof STATUS_OPTIONS)[number]["value"]` came out as plain `string`
 * either way, so `CatalogueEntryStatus` was doing nothing and
 * `statusToneFor`'s signature was unchanged from the plain `string` it
 * replaced. Typing `STATUS_OPTIONS` *against* this union below (rather than
 * deriving the union from it) is what actually catches a value added to one
 * without the other agreeing.
 */
export type CatalogueEntryStatus = "draft" | "active" | "deprecated" | "withdrawn";

/**
 * The four `CatalogueEntryStatus` values. Hardcoded, unlike every other
 * facet (`AdminCatalogueFilterPanel`'s own docstring), because this set is a
 * stable part of the domain model, not administrator-editable registry
 * state - there is nowhere on the wire to discover it from in browse mode
 * (facets, with counts, exist only on the search surface).
 *
 * Its own module, not a constant inside `admin-catalogue-filter-panel.tsx`
 * (issue #289 review): a `.tsx` file that exports a non-component trips
 * `react-refresh/only-export-components` - `limits.ts`'s own precedent for
 * this exact fix. Shared by the filter panel and the active-filter-chip
 * label resolver on the list page, so both read the identical labels.
 *
 * Typed against `CatalogueEntryStatus`, not `{ value: string; label: string
 * }[]`: a value here that isn't already in that union fails to compile,
 * rather than silently widening. Stays a plain mutable array (not `as
 * const`), so this remains assignable to `AdminCatalogueFilterPanel`'s
 * mutable `FacetOption[]`.
 */
export const STATUS_OPTIONS: { value: CatalogueEntryStatus; label: string }[] = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "deprecated", label: "Deprecated" },
  { value: "withdrawn", label: "Withdrawn" },
];

/**
 * Maps a `CatalogueEntryStatus` value to the `StatusBadge` tone that renders
 * it (docs/architecture/design-system.md). Active reuses Published's design
 * tone and Withdrawn reuses Deprecated's - no separate colour was drafted
 * for either. An unrecognised value falls back to `neutral` rather than
 * throwing, since a badge degrading to a muted pill is preferable to a
 * screen crashing over an unexpected status string - real server data is
 * `string`, not the closed union, so that fallback stays. `status-options.
 * test.ts` iterates `STATUS_OPTIONS` and asserts none of them hits this
 * fallback, so an added lifecycle value with no case here fails a test
 * instead of silently rendering as `neutral`.
 */
export function statusToneFor(status: CatalogueEntryStatus | (string & {})): StatusTone {
  switch (status) {
    case "draft":
      return "draft";
    case "active":
      return "active";
    case "deprecated":
    case "withdrawn":
      return "deprecated";
    default:
      return "neutral";
  }
}
