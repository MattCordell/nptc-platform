import type { Ref } from "react";

import { asVersionConflict } from "../api/conflicts.ts";
import { Button } from "../components/button.tsx";
import type { CollisionWarning, DesignationWarning } from "./collision-notice.tsx";
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

function WarningItem({
  warning,
  onAcknowledge,
}: {
  warning: DesignationWarning;
  onAcknowledge?: (warning: CollisionWarning) => void;
}) {
  switch (warning.kind) {
    case "collision":
      // The same term on another live entry is allowed, so the action is to
      // confirm it is intended (FR-05).
      return (
        <li>
          &ldquo;{warning.term}&rdquo; is also on {warning.business_key} &mdash;{" "}
          {warning.preferred_term}
          {onAcknowledge !== undefined && (
            <>
              {" "}
              <Button
                type="button"
                variant="secondary"
                aria-label={`Acknowledge ${warning.term}`}
                onClick={() => onAcknowledge(warning)}
              >
                Acknowledge
              </Button>
            </>
          )}
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

function warningKey(warning: DesignationWarning): string {
  return warning.kind === "collision"
    ? `${warning.term}-${warning.business_key}`
    : "length";
}

/**
 * Warnings that rode back on a write that succeeded, so each is worth a look
 * and none blocked the save. A duplicate the editor already acknowledged is
 * left out.
 */
export function WarningList({
  warnings,
  acknowledged,
  onAcknowledge,
}: {
  warnings: DesignationWarning[];
  acknowledged: ReadonlySet<string>;
  onAcknowledge?: (warning: CollisionWarning) => void;
}) {
  const shown = warnings.filter(
    (warning) => warning.kind !== "collision" || !acknowledged.has(warning.term),
  );
  if (shown.length === 0) {
    return null;
  }
  return (
    <>
      <h3>Check these</h3>
      <p>These changes were saved. None of the warnings blocked the save.</p>
      {onAcknowledge !== undefined && shown.some((w) => w.kind === "collision") && (
        <p>
          A term can be on two entries, because two entries can legitimately share a
          synonym. Acknowledge a duplicate to confirm it is intended and stop it being
          reported on every save.
        </p>
      )}
      <ul>
        {shown.map((warning) => (
          <WarningItem
            key={warningKey(warning)}
            warning={warning}
            onAcknowledge={onAcknowledge}
          />
        ))}
      </ul>
    </>
  );
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
  acknowledged,
  onAcknowledge,
}: {
  run: SaveRun;
  headingRef: Ref<HTMLHeadingElement>;
  acknowledged: ReadonlySet<string>;
  onAcknowledge: (warning: CollisionWarning) => void;
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

      <WarningList
        warnings={warnings}
        acknowledged={acknowledged}
        onAcknowledge={onAcknowledge}
      />
    </section>
  );
}
