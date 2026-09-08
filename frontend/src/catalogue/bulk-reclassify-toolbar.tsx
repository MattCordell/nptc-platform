import { Button } from "../components/button.tsx";

function selectionCountText(count: number): string {
  return `${count} entr${count === 1 ? "y" : "ies"} selected.`;
}

/**
 * The launch point for #63's bulk reclassify - shown only once at least one
 * row is selected on the admin catalogue list (issue #267's own selection
 * state, `Map<business_key, row_version>`).
 *
 * The cap on how many entries one reclassify can cover is enforced inside
 * the dialog's own submit gate, not here (issue #63 plan): disabling this
 * button past 100 selected would tell the operator nothing about *why*, and
 * they may still want to deselect down to the cap from inside the dialog
 * rather than back out to the list first.
 */
export function BulkReclassifyToolbar({
  selectedCount,
  onLaunch,
}: {
  selectedCount: number;
  onLaunch: () => void;
}) {
  if (selectedCount === 0) {
    return null;
  }
  return (
    <div role="group" aria-label="Bulk reclassify" className="flex items-center gap-2">
      <p>{selectionCountText(selectedCount)}</p>
      <Button type="button" onClick={onLaunch}>
        Reclassify selected
      </Button>
    </div>
  );
}
