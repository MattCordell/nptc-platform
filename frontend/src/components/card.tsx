import type { ComponentPropsWithoutRef } from "react";

type CardProps = ComponentPropsWithoutRef<"div">;

/**
 * A white surface with a hairline border and the card radius
 * (docs/architecture/design-system.md). It has one padding and no variants,
 * and no shadow: separation comes from the border alone. A plain `div` -
 * callers add `role` or `aria-labelledby` when a card is a region.
 */
export function Card({ className, ...rest }: CardProps) {
  return (
    <div
      {...rest}
      className={[
        "rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)] p-6",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
