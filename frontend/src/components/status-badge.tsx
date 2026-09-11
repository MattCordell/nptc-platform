export type StatusTone = "draft" | "active" | "deprecated" | "neutral";

type StatusBadgeProps = {
  tone: StatusTone;
  label: string;
};

const TONE_CLASSES: Record<StatusTone, string> = {
  draft: "text-[var(--color-status-draft-text)] bg-[var(--color-status-draft-bg)]",
  active: "text-[var(--color-status-active-text)] bg-[var(--color-status-active-bg)]",
  deprecated:
    "text-[var(--color-status-deprecated-text)] bg-[var(--color-status-deprecated-bg)]",
  neutral: "text-[var(--color-status-neutral-text)] bg-[var(--color-status-neutral-bg)]",
};

/**
 * A lifecycle-status pill (docs/architecture/design-system.md). Status is
 * always paired with a text label here, never colour alone, per the design
 * notes' voice rule. Kept status-agnostic - it takes a `tone`, not a
 * catalogue status value - so it stays a generic `components/` primitive;
 * `statusToneFor` in `catalogue/status-options.ts` maps real status values
 * onto these tones.
 */
export function StatusBadge({ tone, label }: StatusBadgeProps) {
  return (
    <span
      className={[
        "rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-medium",
        TONE_CLASSES[tone],
      ].join(" ")}
    >
      {label}
    </span>
  );
}
