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
 * `conflicts` is empty whenever the concurrent edit touched a *different*
 * field: the entry still moved, so this save is still refused, but there is no
 * field-level disagreement to show. Both shapes have to read correctly.
 */
function VersionConflictNotice({ body }: { body: VersionConflictBody }) {
  const changedAt = body.changed_at === null ? null : new Date(body.changed_at);
  return (
    <div>
      <p>
        Someone else changed this entry while you had it open
        {body.changed_by === null ? "" : `, most recently ${body.changed_by}`}
        {changedAt === null ? "" : ` at ${changedAt.toLocaleString()}`}. Nothing has been
        saved.
      </p>
      {body.conflicts.length > 0 && (
        <>
          <p>What you sent, and what the entry holds now:</p>
          <ul>
            {body.conflicts.map((conflict) => (
              <li key={conflict.field}>
                <strong>{conflict.field}</strong>: you sent{" "}
                {formatValue(conflict.submitted)}; it is now{" "}
                {formatValue(conflict.current)}
              </li>
            ))}
          </ul>
        </>
      )}
      {/* Not "reload the page": `useAmendDesignation` refetches the entry on
          this refusal, so by the time this is read the screen behind the
          dialog already holds their change (review finding 3). Advice the
          screen does not carry out is worse than no advice. */}
      <p>
        The entry has been reloaded with their change. Check yours is still needed, then
        save it again.
      </p>
    </div>
  );
}

/**
 * `submitted`/`current` are deliberately untyped on the wire - a term, a
 * status, a flag - so they are rendered as quoted text rather than assumed to
 * be strings.
 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "nothing";
  }
  return typeof value === "string" ? `"${value}"` : JSON.stringify(value);
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
