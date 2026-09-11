import type { StatusTone } from "../components/status-badge.tsx";

/**
 * The four `CatalogueEntryStatus` values, matching
 * `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` (derived from the same
 * enum server-side). Hardcoded, unlike every other facet
 * (`AdminCatalogueFilterPanel`'s own docstring), because this set is a
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
 * `satisfies`, not a `: { value: string; label: string }[]` annotation: that
 * would widen every `value` to `string` and lose the literal types
 * `CatalogueEntryStatus` derives below - `satisfies` checks the shape
 * without widening it, and (unlike `as const`) keeps the array mutable, so
 * this stays assignable everywhere a plain `{ value; label }[]` is expected
 * (`AdminCatalogueFilterPanel`'s `FacetOption[]`).
 */
export const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "deprecated", label: "Deprecated" },
  { value: "withdrawn", label: "Withdrawn" },
] satisfies { value: string; label: string }[];

/**
 * The lifecycle values `statusToneFor` maps by name, derived from
 * `STATUS_OPTIONS` so the two cannot silently drift apart (PR #327 review).
 */
export type CatalogueEntryStatus = (typeof STATUS_OPTIONS)[number]["value"];

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
