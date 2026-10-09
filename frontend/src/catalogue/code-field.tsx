import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import { EntryBindings } from "./entry-bindings.tsx";
import { ProcedurePicker } from "./procedure-picker.tsx";
import type { CodeSelection } from "./use-code-selection.ts";

/**
 * The SNOMED CT code of the entry, inside the edit form (FR-06, FR-08, FR-26).
 *
 * An entry holds at most one active binding (FR-08), so the picker adds a code
 * when there is none and replaces the active one when there is. A picked code
 * is not saved until the form's Save. Its FSN and AU preferred term come from
 * the terminology server, and nothing here lets the editor type either.
 *
 * Retired codes stay listed, read only, with their reason and any successor.
 */

type Binding = components["schemas"]["Binding"];

export const CODE_FIELD_ID = "entry-code-search";

function statusText(active: boolean | null): string {
  if (active === null) {
    return "not reported by the terminology server";
  }
  return active ? "active" : "inactive";
}

function PickedConcept({
  selection,
  activeCode,
  onClear,
}: {
  selection: Exclude<CodeSelection, { status: "none" }>;
  activeCode: string | null;
  onClear: () => void;
}) {
  if (selection.status === "checking") {
    return <p>Checking {selection.code} against the terminology server…</p>;
  }
  if (selection.status === "unresolved") {
    return (
      <div className="flex flex-col gap-2">
        <p>{selection.message}</p>
        <div>
          <Button type="button" variant="secondary" onClick={onClear}>
            Clear the chosen code
          </Button>
        </div>
      </div>
    );
  }
  const { concept } = selection;
  return (
    <div className="flex flex-col gap-2">
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
        <dt className="text-[var(--color-text-muted)]">Chosen code</dt>
        <dd className="m-0">
          <CodeChip code={concept.code} />
        </dd>
        <dt className="text-[var(--color-text-muted)]">Fully specified name</dt>
        <dd className="m-0">{concept.fsn}</dd>
        <dt className="text-[var(--color-text-muted)]">AU preferred term</dt>
        <dd className="m-0">{concept.auPreferredTerm ?? "not reported"}</dd>
        <dt className="text-[var(--color-text-muted)]">Status</dt>
        <dd className="m-0">{statusText(concept.active)}</dd>
      </dl>
      <p className="m-0">
        {activeCode === null
          ? "Saving binds this code to the entry."
          : `Saving retires ${activeCode} and binds this code in its place.`}
      </p>
      <div>
        <Button type="button" variant="secondary" onClick={onClear}>
          Clear the chosen code
        </Button>
      </div>
    </div>
  );
}

export function CodeField({
  bindings,
  selection,
  error,
  onPick,
  onClear,
  onRetire,
}: {
  bindings: Binding[];
  selection: CodeSelection;
  error?: string;
  onPick: (code: string) => void;
  onClear: () => void;
  onRetire: (code: string) => void;
}) {
  const active = bindings.find((binding) => binding.status === "active") ?? null;

  return (
    <div className="flex flex-col gap-3">
      <h3>SNOMED CT code</h3>
      {active === null ? (
        <p className="m-0">No SNOMED CT code is bound to this entry.</p>
      ) : (
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-[var(--color-text-muted)]">Bound code</dt>
          <dd className="m-0">
            <CodeChip code={active.code} />
          </dd>
          <dt className="text-[var(--color-text-muted)]">Fully specified name</dt>
          <dd className="m-0">{active.fsn}</dd>
          <dt className="text-[var(--color-text-muted)]">AU preferred term</dt>
          <dd className="m-0">{active.au_preferred_term ?? "not reported"}</dd>
        </dl>
      )}
      {active !== null && (
        <div>
          <Button
            type="button"
            variant="danger"
            aria-label={`Retire ${active.code} without a replacement`}
            onClick={() => onRetire(active.code)}
          >
            Retire without a replacement
          </Button>
        </div>
      )}

      <ProcedurePicker
        id={CODE_FIELD_ID}
        label={active === null ? "SNOMED CT code" : "Replacement SNOMED CT code"}
        error={error}
        onPick={onPick}
      />

      {selection.status !== "none" && (
        <PickedConcept
          selection={selection}
          activeCode={active?.code ?? null}
          onClear={onClear}
        />
      )}

      <EntryBindings bindings={bindings} />
    </div>
  );
}
