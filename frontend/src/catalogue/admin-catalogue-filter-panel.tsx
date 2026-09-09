import { useId, useMemo, useState } from "react";

import {
  usePropertyDefinitions,
  usePropertyValueOptions,
  usePropertyValueResolve,
} from "../api/queries.ts";
import { Checkbox } from "../components/checkbox.tsx";
import { Field } from "../components/field.tsx";
import { STATUS_OPTIONS } from "./status-options.ts";
import { useDebouncedValue } from "./use-debounced-value.ts";

/**
 * The filter panel for the admin catalogue list screen (issue #267, FR-16).
 *
 * FR-16 names discipline, subgroup, specimen and status as the minimum
 * filterable facets. Status is a core `catalogue_entry` column, not a
 * registry property, and (unlike discipline/subgroup/specimen) has no
 * `PropertyDefinition` to enumerate itself from - so it is the one facet
 * declared here rather than discovered, mirroring `nptc.catalogue.facets`'
 * own "declared, not derived" treatment of the identical column
 * server-side (ADR-0032).
 *
 * **Only `concept_picker`-controlled, filterable, active properties get a
 * facet in browse mode** (open question resolved against the real registry
 * fixture, issue #267 plan): a `text`/`textarea`/`number`/`uri` filterable
 * property has no sensible checkbox-list control, and browse mode has no
 * facet-count endpoint to derive one from at all (ADR-0032 - counts exist
 * only on the search surface). Rendering those anyway would mean inventing
 * range/prefix UI the acceptance criteria does not ask for; they are simply
 * omitted here rather than given a half-built control. A deprecated
 * property is omitted too - it can no longer be set on a new value, and a
 * facet for it would let an administrator filter by a state nothing being
 * edited can enter again.
 */

interface FacetOption {
  value: string;
  label: string;
}

function FacetGroup({
  legend,
  options,
  selected,
  onToggle,
  hint,
}: {
  legend: string;
  options: FacetOption[];
  selected: string[];
  onToggle: (value: string) => void;
  hint?: string;
}) {
  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0">
      <legend className="text-sm font-medium text-[var(--color-text)]">{legend}</legend>
      {hint ? <p className="text-sm text-[var(--color-text-muted)]">{hint}</p> : null}
      {options.map((option) => (
        <Checkbox
          key={option.value}
          label={option.label}
          checked={selected.includes(option.value)}
          onChange={() => onToggle(option.value)}
        />
      ))}
    </fieldset>
  );
}

/**
 * One coded, filterable property's facet - a text box that narrows
 * `usePropertyValueOptions` (FR-52's text-filter primitive) plus a checkbox
 * per offerable value, matching `property-controls/concept-picker.tsx`'s own
 * data source but multi-select rather than single-value: a facet's several
 * selected values are OR-ed (ADR-0032), unlike a recorded property value.
 */
function PropertyFacetGroup({
  propertyKey,
  label,
  selected,
  onToggle,
}: {
  propertyKey: string;
  label: string;
  selected: string[];
  onToggle: (value: string) => void;
}) {
  const filterId = useId();
  const [filterText, setFilterText] = useState("");
  const debouncedFilter = useDebouncedValue(filterText, 400);
  const options = usePropertyValueOptions(propertyKey, debouncedFilter);
  // The same property's *unfiltered* page, read whether or not the facet's
  // own filter text is blank - `usePropertyValueOptions` shares one cache
  // entry per `(key, filter)` pair, so this is a fresh request only the
  // first time a facet is shown; every filtered render after that reads it
  // straight from cache (review round 1, PR #307). A carried value already
  // on this page has a known label without a resolve-by-code request at
  // all, and - unlike the filtered page above - it does not go stale every
  // time the filter text changes, so typing in the filter box no longer
  // fires a fresh resolve for a value the unfiltered page already answered.
  const unfilteredOptions = usePropertyValueOptions(propertyKey, "");

  const fetchedOptions: FacetOption[] = (options.data?.items ?? []).map((item) => ({
    value: item.code,
    label: item.display ?? item.code,
  }));
  const unfilteredLabelByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of unfilteredOptions.data?.items ?? []) {
      map.set(item.code, item.display ?? item.code);
    }
    return map;
  }, [unfilteredOptions.data]);
  // A value already selected but absent from the current fetched page (a
  // retired code, or one the current filter text no longer matches) stays
  // offered - matching `ConceptPickerControl`'s own reasoning for a single
  // value - so unchecking it is still possible without first clearing the
  // filter text back to nothing. Rendered from `selected` unconditionally,
  // never blanked while a page is still pending (review round 1, PR #307):
  // an off-page value stays checked from the first paint, rather than
  // disappearing for one render and reappearing once a page settles.
  const carriedValues = useMemo(
    () =>
      selected.filter(
        (value) => !fetchedOptions.some((option) => option.value === value),
      ),
    // `fetchedOptions` is a new array identity every render (derived from
    // `options.data`) - depending on `options.data` instead keeps this memo
    // stable across renders where neither the page nor the selection
    // actually changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, options.data],
  );
  // Its label is resolved directly by code (issue #306, ADR-0038), unbounded
  // by `DEFAULT_PAGE_SIZE` and independent of the property's current bound
  // value set, for whichever carried values the unfiltered page above did
  // not already answer. Held to `[]` until `options` has actually settled
  // (success or error): before that, `fetchedOptions` is always empty, so
  // every selected value would otherwise look "carried" for one render and
  // fire a resolve request the page itself was about to answer a moment
  // later - a real extra fetch, not just an extra cache read.
  // `.slice(0, 200)` matches the route's own `code` ceiling and
  // `admin-catalogue-list.tsx`'s identical cap on its chip resolver (review
  // round 1, PR #307) - past 200, the request would 422 and every carried
  // checkbox in this facet would fall back to its raw code, including the
  // ones within the ceiling. The two caps also need to agree: both surfaces
  // resolve the same property's codes through one shared query-cache entry
  // (`propertyValueResolveQuery`'s sorted key), so a facet at or under 200
  // unresolved values still shares one fetch between the chip and the panel.
  const carriedCodes = useMemo(
    () =>
      options.isPending
        ? []
        : carriedValues
            .filter((value) => !unfilteredLabelByCode.has(value))
            .slice(0, 200),
    [carriedValues, options.isPending, unfilteredLabelByCode],
  );
  const carriedLabels = usePropertyValueResolve(propertyKey, carriedCodes);
  const carriedLabelByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of carriedLabels.data?.items ?? []) {
      map.set(item.code, item.display ?? item.code);
    }
    return map;
  }, [carriedLabels.data]);
  const carriedOptions: FacetOption[] = carriedValues.map((value) => ({
    value,
    label: unfilteredLabelByCode.get(value) ?? carriedLabelByCode.get(value) ?? value,
  }));

  return (
    <div className="flex flex-col gap-2">
      <Field
        id={filterId}
        label={`Filter ${label}`}
        hint="Type to narrow the list below."
      >
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
          />
        )}
      </Field>
      <FacetGroup
        legend={label}
        options={[...fetchedOptions, ...carriedOptions]}
        selected={selected}
        onToggle={onToggle}
        hint={
          options.isPending && debouncedFilter.length > 0
            ? "Searching…"
            : options.isError
              ? "The list of values could not be loaded. Try again."
              : undefined
        }
      />
    </div>
  );
}

export function AdminCatalogueFilterPanel({
  selections,
  onToggle,
}: {
  /** Keyed by facet alone (`filterSelections`'s own output shape,
   * `router/search-params.ts`). */
  selections: Record<string, string[]>;
  onToggle: (facetKey: string, value: string) => void;
}) {
  const definitions = usePropertyDefinitions();
  const codedFilterableProperties = (definitions.data?.items ?? []).filter(
    (definition) =>
      definition.filterable &&
      definition.status === "active" &&
      definition.form_control.control === "concept_picker",
  );

  return (
    <div className="flex flex-col gap-4">
      <FacetGroup
        legend="Status"
        options={STATUS_OPTIONS}
        selected={selections.status ?? []}
        onToggle={(value) => onToggle("status", value)}
      />
      {codedFilterableProperties.map((definition) => (
        <PropertyFacetGroup
          key={definition.key}
          propertyKey={definition.key}
          label={definition.label}
          selected={selections[definition.key] ?? []}
          onToggle={(value) => onToggle(definition.key, value)}
        />
      ))}
    </div>
  );
}
