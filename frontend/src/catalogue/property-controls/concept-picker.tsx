import { useId, useState } from "react";

import { usePropertyValueOptions } from "../../api/queries.ts";
import { Field } from "../../components/field.tsx";
import { Select } from "../../components/select.tsx";
import type { SelectOption } from "../../components/select.tsx";
import { useDebouncedValue } from "../use-debounced-value.ts";
import type { ControlProps } from "./types.ts";

/**
 * `ControlKind.CONCEPT_PICKER` - the `code` datatype (issue #150's
 * successor for property values rather than the entry's own binding).
 *
 * Composes `Select` (issue #210's native-select baseline), fed by a
 * separate filter `Field`: the filter text narrows what
 * `usePropertyValueOptions` returns server-side (FR-52's own text-filter
 * primitive), and the select lists whatever comes back. This never
 * branches on `binding_target` - it asks `usePropertyValueOptions
 * (propertyKey, ...)`, which resolves that server-side (issue #247) - so
 * the same component serves `specimen` (SNOMED value set) and
 * `discipline`/`subgroup` (local code systems) identically.
 *
 * The currently held code is always offered as an option, even when it
 * falls outside the current filter's results - the same reasoning
 * `CheckboxGroup` gives for carrying through a value with no matching
 * option: a retired or superseded code already recorded here must stay
 * choosable (in effect, unchanged) rather than disappearing the moment an
 * editor types a filter that does not happen to match it.
 */
export function ConceptPickerControl({
  id,
  label,
  hint,
  error,
  value,
  onChange,
  propertyKey,
}: ControlProps) {
  const filterId = useId();
  const [filterText, setFilterText] = useState("");
  const debouncedFilter = useDebouncedValue(filterText, 400);
  const options = usePropertyValueOptions(propertyKey, debouncedFilter);
  const currentCode = typeof value === "string" ? value : "";

  const fetchedOptions: SelectOption[] = (options.data?.items ?? []).map((item) => ({
    value: item.code,
    label: item.display === null ? item.code : `${item.code} — ${item.display}`,
  }));
  const selectOptions =
    currentCode.length > 0 &&
    !fetchedOptions.some((option) => option.value === currentCode)
      ? [
          { value: currentCode, label: `${currentCode} (currently recorded)` },
          ...fetchedOptions,
        ]
      : fetchedOptions;

  const searchHint =
    options.isPending && debouncedFilter.length > 0
      ? "Searching…"
      : options.isError
        ? "The list of values could not be loaded. Try again."
        : undefined;

  return (
    <div className="flex flex-col gap-2">
      <Field id={filterId} label="Filter" hint="Type to narrow the list below.">
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
          />
        )}
      </Field>
      <Select
        label={label}
        id={id}
        hint={searchHint ?? hint}
        error={error}
        options={selectOptions}
        placeholder="Choose a code"
        value={currentCode}
        onChange={(event) =>
          onChange(event.target.value === "" ? null : event.target.value)
        }
      />
    </div>
  );
}
