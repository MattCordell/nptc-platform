import type { ComponentPropsWithoutRef } from "react";

import { Button } from "./button.tsx";

export type ActiveFilter = {
  /** Unique across the whole list; passed back to `onRemove`. */
  key: string;
  facetLabel: string;
  valueLabel: string;
};

type FilterBarProps = {
  activeFilters?: ActiveFilter[];
  onRemove?: (key: string) => void;
  onClearAll?: () => void;
} & Omit<ComponentPropsWithoutRef<"div">, "children">;

const CHIP_CLASSES =
  "min-h-8 cursor-pointer rounded-[var(--radius-pill)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 text-sm text-[var(--color-text)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]";

/**
 * One removable chip per active filter, with a "Clear all filters" button
 * (FR-16). Holds no state; the caller owns what is selected, usually in the
 * URL, and draws the controls that select it: `MultiSelectCombobox` on the
 * public catalogue page, a facet panel on the admin list.
 */
export function FilterBar({
  activeFilters = [],
  onRemove,
  onClearAll,
  className,
  ...rest
}: FilterBarProps) {
  return (
    <div
      {...rest}
      className={["flex flex-col gap-3", className ?? ""].filter(Boolean).join(" ")}
    >
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
              className={CHIP_CLASSES}
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
