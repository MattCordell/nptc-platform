import type { Ref } from "react";

import { asVersionConflict } from "../api/conflicts.ts";
import type { DesignationWarning } from "./collision-notice.tsx";
import { ConflictAttribution } from "./collision-notice.tsx";
import type { FieldOutcome, SaveRun } from "./run-save.ts";

/**
 * The one account of a save run (FR-36): which fields saved, which did not
 * and why. The heading takes focus after a run that left something unsaved,
 * so a keyboard or screen-reader editor lands on what needs attention.
 */

function warningsOf(run: SaveRun): DesignationWarning[] {
  return run.outcomes.flatMap((outcome) =>
    outcome.status === "saved" ? outcome.warnings : [],
  );
}

function WarningItem({ warning }: { warning: DesignationWarning }) {
  switch (warning.kind) {
    case "collision":
      return (
        <li>
          &ldquo;{warning.term}&rdquo; is also on {warning.business_key} &mdash;{" "}
          {warning.preferred_term}
        </li>
      );
    case "length":
      return (
        <li>
          The preferred term is {warning.length} characters long, which is over the
          maximum of {warning.max_length}. Consider shortening it.
        </li>
      );
    default: {
      const unhandled: never = warning;
      return unhandled;
    }
  }
}

function NotSavedItem({
  outcome,
}: {
  outcome: Exclude<FieldOutcome, { status: "saved" }>;
}) {
  const conflict = outcome.status === "failed" ? asVersionConflict(outcome.error) : null;
  return (
    <li>
      <strong>{outcome.change.label}</strong>: {outcome.message}
      {conflict !== null && (
        <>
          <ConflictAttribution
            body={conflict}
            since="while you had it open"
            submittedLabel="you sent"
          />
          <p>
            The entry is reloading with their change. Check yours is still needed, then
            save again.
          </p>
        </>
      )}
    </li>
  );
}

export function SaveSummary({
  run,
  headingRef,
}: {
  run: SaveRun;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const saved = run.outcomes.filter((outcome) => outcome.status === "saved");
  const notSaved = run.outcomes.filter(
    (outcome): outcome is Exclude<FieldOutcome, { status: "saved" }> =>
      outcome.status !== "saved",
  );
  const warnings = warningsOf(run);

  return (
    <section aria-labelledby="save-summary-heading">
      <h2 id="save-summary-heading" ref={headingRef} tabIndex={-1}>
        {notSaved.length === 0 ? "Changes saved" : "Some changes were not saved"}
      </h2>

      {saved.length > 0 && (
        <>
          <h3>Saved</h3>
          <ul>
            {saved.map((outcome) => (
              <li key={outcome.change.id}>{outcome.change.label}</li>
            ))}
          </ul>
        </>
      )}

      {notSaved.length > 0 && (
        <>
          <h3>Not saved</h3>
          <ul>
            {notSaved.map((outcome) => (
              <NotSavedItem key={outcome.change.id} outcome={outcome} />
            ))}
          </ul>
          <p>
            {run.stopped
              ? "The save stopped at the first field that failed. Every field that was not sent is still on screen."
              : "Correct the marked fields and save again."}{" "}
            Only fields that are not yet saved are sent next time.
          </p>
        </>
      )}

      {warnings.length > 0 && (
        <>
          <h3>Check these</h3>
          <p>These changes were saved. None of the warnings blocked the save.</p>
          <ul>
            {warnings.map((warning) => (
              <WarningItem
                key={
                  warning.kind === "collision"
                    ? `${warning.term}-${warning.business_key}`
                    : "length"
                }
                warning={warning}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
