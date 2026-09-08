import { useId, useState } from "react";

import { usePropertyDefinitions, usePropertyValueOptions } from "../api/queries.ts";
import { Checkbox } from "../components/checkbox.tsx";
import { Field } from "../components/field.tsx";
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

//: The four `CatalogueEntryStatus` values, matching
//: `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` (derived from the same
//: enum server-side). Hardcoded, unlike every other facet here, because
//: this set is a stable part of the domain model, not administrator-editable
//: registry state - there is nowhere on the wire to discover it from in
//: browse mode (facets, with counts, exist only on the search surface).
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "deprecated", label: "Deprecated" },
  { value: "withdrawn", label: "Withdrawn" },
];

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

  const fetchedOptions: FacetOption[] = (options.data?.items ?? []).map((item) => ({
    value: item.code,
    label: item.display ?? item.code,
  }));
  // A value already selected but absent from the current fetched page (a
  // retired code, or one the current filter text no longer matches) stays
  // offered - matching `ConceptPickerControl`'s own reasoning for a single
  // value - so unchecking it is still possible without first clearing the
  // filter text back to nothing.
  const carriedOptions: FacetOption[] = selected
    .filter((value) => !fetchedOptions.some((option) => option.value === value))
    .map((value) => ({ value, label: value }));

  return (
    <div className="flex flex-col gap-2">
      <Field id={filterId} label={`Filter ${label}`} hint="Type to narrow the list below.">
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
