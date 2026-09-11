export type ButtonVariant = "primary" | "secondary" | "danger";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-accent)] text-[var(--color-accent-contrast)] border-transparent hover:bg-[var(--color-accent-hover)]",
  secondary:
    "bg-[var(--color-surface)] text-[var(--color-text)] border-[var(--color-border)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]",
  danger:
    "bg-[var(--color-danger)] text-[var(--color-accent-contrast)] border-transparent",
};

/**
 * The base + variant classes `Button` renders, exposed for call sites that
 * need the same look on an element `Button` can't be - e.g. a router `Link`
 * styled as a primary action - without a second, divergent copy of
 * `VARIANT_CLASSES` drifting the next time this file restyles. Kept out of
 * `button.tsx` itself so that file only exports the `Button` component -
 * mixing a component export with a plain function export there defeats
 * Fast Refresh (`react-refresh/only-export-components`).
 */
export function buttonClassName(variant: ButtonVariant = "primary"): string {
  return `rounded-md border px-4 py-2 text-sm font-medium ${VARIANT_CLASSES[variant]}`;
}
