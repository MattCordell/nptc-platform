/**
 * The text-input look (docs/architecture/design-system.md), shared by every
 * screen that renders a raw `<input>` inside a `Field`. Kept out of
 * `field.tsx` so that file only exports the `Field` component
 * (`react-refresh/only-export-components`).
 */
export const INPUT_CLASSES =
  "min-h-10 w-full rounded-[var(--radius-control)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-base text-[var(--color-text)]";
