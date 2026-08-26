import type { ComponentPropsWithoutRef } from "react";

type ButtonVariant = "primary" | "secondary" | "danger";

type ButtonProps = {
  variant?: ButtonVariant;
  /** No default: forces every call site to choose, rather than silently
   * inheriting the native element's "submit" default. */
  type: "button" | "submit" | "reset";
} & Omit<ComponentPropsWithoutRef<"button">, "type">;

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-accent)] text-[var(--color-accent-contrast)] border-transparent",
  secondary:
    "bg-[var(--color-surface)] text-[var(--color-text)] border-[var(--color-border)]",
  danger:
    "bg-[var(--color-danger)] text-[var(--color-accent-contrast)] border-transparent",
};

/**
 * A button that always declares its `type` explicitly (issue #148): inside
 * a `<form>`, an untyped `<button>` defaults to `type="submit"`, which is a
 * frequent source of an accidental submit on what was meant to be a plain
 * action button. `type` is a required prop here, not defaulted, so a caller
 * has to make the choice rather than inherit it by accident.
 *
 * The disabled state is styled with reduced opacity rather than removing
 * the border or background outright, and `:focus-visible` (declared once,
 * globally, in `src/styles/app.css`) is left unset by every variant so the
 * focus ring is never fainter than the default.
 */
export function Button({
  variant = "primary",
  type,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      className={[
        "rounded-md border px-4 py-2 text-sm font-medium",
        VARIANT_CLASSES[variant],
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    />
  );
}
