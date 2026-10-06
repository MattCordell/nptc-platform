/**
 * Whether an entry has an open validation finding (FR-18). It says only that
 * one exists: the finding itself is for administrators. The open state pairs
 * its colour with text and a mark, so it never relies on colour alone.
 */
export function FindingIndicator({ open }: { open: boolean }) {
  if (!open) {
    return <span className="text-[var(--color-text-muted)]">None</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-[var(--color-danger-surface)] px-2 py-0.5 text-xs font-medium text-[var(--color-danger)]">
      <span aria-hidden="true">!</span>
      Open finding
    </span>
  );
}
