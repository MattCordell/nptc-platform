import type { components } from "../api/schema.ts";
import { CodeChip } from "../components/code-chip.tsx";

type Binding = components["schemas"]["Binding"];

/** Why a binding was retired and what replaced it, or a dash when neither is recorded. */
export function BindingRetirement({ binding }: { binding: Binding }) {
  if (binding.retirement_reason === null && binding.replaced_by_code === null) {
    return <span className="text-[var(--color-text-muted)]">—</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      {binding.retirement_reason !== null ? (
        <span>{binding.retirement_reason}</span>
      ) : null}
      {binding.replaced_by_code !== null ? (
        <span>
          Replaced by <CodeChip code={binding.replaced_by_code} />
        </span>
      ) : null}
    </div>
  );
}
