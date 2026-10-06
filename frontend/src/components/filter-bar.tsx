import { useId } from "react";
import type { ComponentPropsWithoutRef } from "react";

import { Button } from "./button.tsx";
import { Select } from "./select.tsx";

export type FilterOption = {
  value: string;
  label: string;
  /** Result count for this option (FR-16). Omit when counts are unknown, for
   * example while browsing rather than searching. */
  count?: number;
};

export type FilterToggleGroup = {
  label: string;
  options: FilterOption[];
  selected: readonly string[];
  onToggle: (value: string) => void;
};

export type FilterDropdown = {
  id?: string;
  label: string;
  options: FilterOption[];
  /** The empty string means "no filter". */
  value: string;
  onChange: (value: string) => void;
  /** The label of the empty-valued first option, such as "Any". */
  placeholder?: string;
};

export type ActiveFilter = {
  /** Unique across the whole list; passed back to `onRemove`. */
  key: string;
  facetLabel: string;
  valueLabel: string;
};

type FilterBarProps = {
  toggleGroups?: FilterToggleGroup[];
  dropdowns?: FilterDropdown[];
  activeFilters?: ActiveFilter[];
  onRemove?: (key: string) => void;
  onClearAll?: () => void;
} & Omit<ComponentPropsWithoutRef<"div">, "children">;

const PILL_BASE =
  "min-h-8 cursor-pointer rounded-[var(--radius-pill)] border px-3 py-1 text-sm";

const PILL_OFF =
  "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]";

// A check mark and a heavier weight carry the pressed state as well as the
// fill, so it never rests on colour alone (NFR-31).
const PILL_ON =
  "border-transparent bg-[var(--color-accent)] font-semibold text-[var(--color-accent-contrast)] hover:bg-[var(--color-accent-hover)]";

function optionText({ label, count }: FilterOption): string {
  return count === undefined ? label : `${label} (${count})`;
}

function ToggleGroup({ label, options, selected, onToggle }: FilterToggleGroup) {
  const labelId = useId();

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      className="flex flex-wrap items-center gap-2"
    >
      <span id={labelId} className="text-sm font-medium text-[var(--color-text)]">
        {label}
      </span>
      {options.map((option) => {
        const pressed = selected.includes(option.value);
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            onClick={() => onToggle(option.value)}
            className={`${PILL_BASE} ${pressed ? PILL_ON : PILL_OFF}`}
          >
            {pressed ? <span aria-hidden="true">✓ </span> : null}
            {optionText(option)}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The facet controls of a listing (FR-16): pill toggles for the few-valued
 * facets, dropdowns for the many-valued ones, and below them one removable
 * chip per active filter with a "Clear all filters" button. Holds no state;
 * the caller owns what is selected, usually in the URL.
 *
 * Each part renders only when given something, so a screen that keeps its own
 * facet panel can use the chip row alone.
 */
export function FilterBar({
  toggleGroups = [],
  dropdowns = [],
  activeFilters = [],
  onRemove,
  onClearAll,
  className,
  ...rest
}: FilterBarProps) {
  const hasControls = toggleGroups.length > 0 || dropdowns.length > 0;

  return (
    <div
      {...rest}
      className={["flex flex-col gap-3", className ?? ""].filter(Boolean).join(" ")}
    >
      {hasControls ? (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          {toggleGroups.map((group) => (
            <ToggleGroup key={group.label} {...group} />
          ))}
          {dropdowns.map((dropdown) => (
            <Select
              key={dropdown.label}
              id={dropdown.id}
              label={dropdown.label}
              value={dropdown.value}
              placeholder={dropdown.placeholder}
              onChange={(event) => dropdown.onChange(event.target.value)}
              options={dropdown.options.map((option) => ({
                value: option.value,
                label: optionText(option),
              }))}
              className="min-h-10"
            />
          ))}
        </div>
      ) : null}

      {activeFilters.length > 0 ? (
        <div
          role="group"
          aria-label="Active filters"
          className="flex flex-wrap items-center gap-2"
        >
          {activeFilters.map(({ key, facetLabel, valueLabel }) => (
            <button
              key={key}
              type="button"
              aria-label={`Remove filter ${facetLabel}: ${valueLabel}`}
              onClick={() => onRemove?.(key)}
              className={`${PILL_BASE} ${PILL_OFF}`}
            >
              {facetLabel}: {valueLabel}
              <span aria-hidden="true"> ✕</span>
            </button>
          ))}
          <Button type="button" variant="secondary" onClick={() => onClearAll?.()}>
            Clear all filters
          </Button>
        </div>
      ) : null}
    </div>
  );
}
