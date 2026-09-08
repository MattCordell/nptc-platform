import { Link } from "@tanstack/react-router";

import type { CollisionBody, VersionConflictBody } from "../api/conflicts.ts";
import { asCollisionError, asVersionConflict, refusalDetail } from "../api/conflicts.ts";
import type { components } from "../api/schema.ts";

/**
 * Turning a refusal into something an editor can act on (FR-05, FR-38).
 *
 * PRD §17.2 item 5: "Errors surfaced to the user in language that says what to
 * do next, not a stack trace or an HTTP status." Nothing here renders a status
 * code, and every message ends with the action available.
 */

export type CollisionWarning = components["schemas"]["CollisionWarning"];

const FALLBACK_REFUSAL =
  "This could not be saved. Check the details and try again, or contact an administrator " +
  "if the problem persists.";

/** A link to the entry a collision names, by its public identifier (FR-03). */
function EntryLink({
  businessKey,
  preferredTerm,
}: {
  businessKey: string;
  preferredTerm: string;
}) {
  return (
    <Link to="/catalogue/$businessKey" params={{ businessKey }}>
      {businessKey} — {preferredTerm}
    </Link>
  );
}

function CollisionNotice({ body }: { body: CollisionBody }) {
  const many = body.collisions.length > 1;
  return (
    <div>
      <p>
        This term is already in use {many ? "on these entries" : "on another entry"}, once
        case, spacing and punctuation are ignored:
      </p>
      <ul>
        {body.collisions.map((collision) => (
          <li key={`${collision.business_key}-${collision.preferred_term}`}>
            <EntryLink
              businessKey={collision.business_key}
              preferredTerm={collision.preferred_term}
            />
          </li>
        ))}
      </ul>
      <p>
        Nothing has been saved. Choose a different term, or open{" "}
        {many ? "those entries" : "that entry"} and resolve it there first.
      </p>
    </div>
  );
}

/**
 * `submitted`/`current` are deliberately untyped on the wire - a term, a
 * status, a flag - so they are rendered as quoted text rather than assumed to
 * be strings.
 */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "nothing";
  }
  return typeof value === "string" ? `"${value}"` : JSON.stringify(value);
}

/**
 * The who/when a concurrent edit happened, plus the field-level diff if any
 * (`conflicts` is empty whenever the concurrent edit touched a *different*
 * field - the entry still moved, so the save is still refused, but there is
 * no field-level disagreement to show). Shared between the single-entry 409
 * notice below and the bulk reclassify per-entry conflict summary (issue
 * #63) - both read an identical `VersionConflictResponse` body, and only the
 * "since when" clause and the submitted/current verb differ by context.
 */
export function ConflictAttribution({
  body,
  since,
  submittedLabel = "sent",
}: {
  body: VersionConflictBody;
  /** The clause naming when the concurrent edit happened, e.g. "while you
   * had it open" or "since this entry was selected". */
  since: string;
  /** The verb describing what was submitted, e.g. "you sent" (default
   * "sent") - varies because a single-entry dialog can say "you", while a
   * bulk outcome list is describing a batch, not one person's own action. */
  submittedLabel?: string;
}) {
  const changedAt = body.changed_at === null ? null : new Date(body.changed_at);
  return (
    <>
      <p>
        Someone else changed this entry {since}
        {body.changed_by === null ? "" : `, most recently ${body.changed_by}`}
        {changedAt === null ? "" : ` at ${changedAt.toLocaleString()}`}.
      </p>
      {body.conflicts.length > 0 && (
        <>
          <p>What {submittedLabel}, and what the entry holds now:</p>
          <ul>
            {body.conflicts.map((conflict) => (
              <li key={conflict.field}>
                <strong>{conflict.field}</strong>: {submittedLabel}{" "}
                {formatValue(conflict.submitted)}; it is now{" "}
                {formatValue(conflict.current)}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function VersionConflictNotice({ body }: { body: VersionConflictBody }) {
  return (
    <div>
      <ConflictAttribution
        body={body}
        since="while you had it open"
        submittedLabel="you sent"
      />
      <p>Nothing has been saved.</p>
      {/* Not "reload the page": `useAmendDesignation` refetches the entry on
          this refusal, so the screen behind the dialog is already fetching
          their change. Present tense, because it says so the moment the 409
          lands - the refetch has not resolved yet and can itself fail, and a
          claim that it is done would sit next to the refresh banner saying it
          is not (PR #238 review). */}
      <p>
        The entry is reloading with their change. Check yours is still needed, then save
        it again.
      </p>
    </div>
  );
}

/**
 * What a failed write shows in `Form`'s `formError` slot.
 *
 * The two rich 409s get their own layout; everything else falls back to the
 * server's own sentence, which `nptc.api.errors` writes to be client-facing
 * (never `str(exc)`, never naming a role or an internal id). A refusal with no
 * usable sentence - an empty body, or FastAPI's `ValidationError` array - gets
 * generic wording rather than `[object Object]`.
 */
export function RefusalNotice({ error }: { error: unknown }) {
  const collision = asCollisionError(error);
  if (collision !== null) {
    return <CollisionNotice body={collision} />;
  }
  const conflict = asVersionConflict(error);
  if (conflict !== null) {
    return <VersionConflictNotice body={conflict} />;
  }
  return <p>{refusalDetail(error) ?? FALLBACK_REFUSAL}</p>;
}
