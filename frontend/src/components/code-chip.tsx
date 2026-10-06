type CodeChipProps = {
  /** A string, never a number: a SNOMED CT code can pass the safe integer
   * range and lose leading zeros if coerced (FR-06). */
  code: string;
};

/**
 * A code shown as the design system's inline "code chip": monospaced, on the
 * code-background token, left-aligned and never truncated, so a reader can
 * compare it digit by digit against another system
 * (docs/architecture/design-system.md). It renders its text exactly as given.
 */
export function CodeChip({ code }: CodeChipProps) {
  return (
    <code className="inline-block rounded-[var(--radius-control)] bg-[var(--color-code-bg)] px-1.5 py-0.5 text-left font-mono text-sm whitespace-nowrap text-[var(--color-code-text)]">
      {code}
    </code>
  );
}
