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
 * The unavailable state is styled with reduced opacity rather than removing
 * the border or background outright, and `:focus-visible` (declared once,
 * globally, in `src/styles/app.css`) is left unset by every variant so the
 * focus ring is never fainter than the default.
 *
 * `aria-disabled` gets that same styling, deliberately (issue #210). A
 * button can be unavailable two ways and they are not interchangeable:
 * `disabled` removes it from the tab order, which strands a keyboard user's
 * focus if it happens under them mid-action, while `aria-disabled` says
 * "refused" and keeps the control focusable and announced. Screens behind
 * this need the second kind - a submit or a Cancel during a save - and if
 * the styling only followed `disabled`, each would reproduce half of it at
 * its own call site and drift.
 */
export function Button({
  variant = "primary",
  type,
  className,
  disabled,
  "aria-disabled": ariaDisabled,
  ...rest
}: ButtonProps) {
  const unavailable =
    disabled === true || ariaDisabled === true || ariaDisabled === "true";

  return (
    <button
      type={type}
      disabled={disabled}
      aria-disabled={ariaDisabled}
      className={[
        "rounded-md border px-4 py-2 text-sm font-medium",
        VARIANT_CLASSES[variant],
        unavailable ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    />
  );
}
