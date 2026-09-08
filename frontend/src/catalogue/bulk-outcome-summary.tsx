import { forwardRef } from "react";

import type { components } from "../api/schema.ts";
import { DataTable } from "../components/data-table.tsx";
import { ConflictAttribution } from "./collision-notice.tsx";

type BulkSavePropertyValuesResult = components["schemas"]["BulkSavePropertyValuesResult"];
type BulkPropertyOutcomeItem = components["schemas"]["BulkPropertyOutcomeItem"];

export function tallyText(result: BulkSavePropertyValuesResult): string {
  return (
    `${result.applied} applied, ${result.unchanged} unchanged, ` +
    `${result.conflict} conflict${result.conflict === 1 ? "" : "s"}, ` +
    `${result.not_found} not found.`
  );
}

/**
 * Why one entry was skipped - the two `VersionConflictResponse` shapes an
 * embedded per-entry `conflict` outcome can carry, plus `not-found`.
 *
 * **The equal-version case is not an ordinary conflict.** An entry deleted
 * and recreated under the same `business_key` mid-batch can return
 * `current_row_version === expected_row_version`, with no attribution and
 * no field diff (`nptc.catalogue.property_values` - see ADR-0035). Rendering
 * `ConflictAttribution` for that would read as a version delta with nothing
 * behind it - no who, no when, no field named - which looks like a bug
 * rather than what actually happened. This is its own branch, not a generic
 * fallback, precisely because the two need different sentences.
 */
function SkipReason({ outcome }: { outcome: BulkPropertyOutcomeItem }) {
  if (outcome.status === "not-found") {
    return <p>No longer exists - it may have been deleted since it was selected.</p>;
  }
  const conflict = outcome.conflict;
  if (!conflict) {
    // Not reachable given the current server contract (a `conflict` status
    // always carries a body), but the wire type marks it optional/nullable,
    // and an empty "Why it was skipped" cell is the one presentation worse
    // than a generic sentence (PR #290 review).
    return <p>This entry was skipped, but why is not known.</p>;
  }
  if (conflict.expected_row_version === conflict.current_row_version) {
    return (
      <p>This entry was replaced while the change was running, so it was skipped.</p>
    );
  }
  return (
    <ConflictAttribution
      body={conflict}
      since="since this batch selected it"
      submittedLabel="the batch sent"
    />
  );
}

/**
 * Shown on the list screen after a bulk reclassify completes - a durable
 * record of what happened, not tied to the dialog's own lifetime, so an
 * operator can still see it after closing what was on screen (issue #63
 * plan: "the results panel is the durable record of what to revisit").
 *
 * Forwards `ref` to its own `<section>` (with `tabIndex={-1}`) so the caller
 * can move focus here once the dialog that produced this result unmounts -
 * `Dialog`'s own focus-restore (`dialog.tsx`) targets whatever triggered it,
 * which is the "Reclassify selected" toolbar button, and that button
 * disappears the moment the selection it completed against is cleared,
 * dropping focus to `<body>` for a keyboard or screen-reader operator right
 * as this section appears (PR #290 review).
 */
export const BulkOutcomeSummary = forwardRef<
  HTMLElement,
  { result: BulkSavePropertyValuesResult; propertyLabel: string }
>(function BulkOutcomeSummary({ result, propertyLabel }, ref) {
  const skipped = result.outcomes.filter(
    (outcome) => outcome.status === "conflict" || outcome.status === "not-found",
  );

  return (
    <section ref={ref} tabIndex={-1} aria-labelledby="bulk-reclassify-results-heading">
      <h2 id="bulk-reclassify-results-heading">Reclassify {propertyLabel}: results</h2>
      <p>{tallyText(result)}</p>
      <DataTable
        caption={`Entries skipped from this ${propertyLabel} reclassify`}
        columns={[
          {
            key: "business_key",
            header: "Code",
            isRowHeader: true,
            render: (outcome) => outcome.business_key,
          },
          {
            key: "reason",
            header: "Why it was skipped",
            render: (outcome) => <SkipReason outcome={outcome} />,
          },
        ]}
        rows={skipped}
        getRowKey={(outcome) => outcome.business_key}
        emptyState="Nothing was skipped."
      />
    </section>
  );
});
