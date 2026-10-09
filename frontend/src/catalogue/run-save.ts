import {
  asCollisionError,
  asPropertyValidationError,
  asVersionConflict,
  refusalDetail,
} from "../api/conflicts.ts";
import { ApiError } from "../api/unwrap.ts";
import type { ChosenConcept } from "./code-binding.ts";
import type { DesignationWarning } from "./collision-notice.tsx";

/**
 * The save run behind the edit form's one Save button (FR-36, FR-38).
 *
 * Changes go out one request each, in the order given, and each success hands
 * its new `row_version` to the next request, because every write bumps it.
 * A refusal of one field changes nothing on the server, so the run carries on.
 * A failure that would repeat on every later request ends the run instead.
 */

interface ValueItem {
  value: unknown;
  justification: string | null;
}

export interface PreferredTermChange {
  kind: "preferred_term";
  id: string;
  label: string;
  /** The stored term, which the amendment route addresses the entry's own term by. */
  currentTerm: string;
  newTerm: string;
}

export interface PropertyChange {
  kind: "property";
  id: string;
  label: string;
  key: string;
  values: ValueItem[];
}

/** Binds `concept`, or, when the entry has an active code, replaces that code with it. */
export interface BindingChange {
  kind: "binding";
  id: string;
  label: string;
  /** The active code the new one replaces, or `null` when the entry has none. */
  currentCode: string | null;
  concept: ChosenConcept;
}

export interface SynonymAmendChange {
  kind: "synonym_amend";
  id: string;
  label: string;
  /** The stored term, which the amendment route addresses the synonym by. */
  currentTerm: string;
  newTerm: string;
}

/** Retires a synonym. The row stays, marked retired. */
export interface SynonymRetireChange {
  kind: "synonym_retire";
  id: string;
  label: string;
  term: string;
}

/** Adds every term as a new synonym, in one request. */
export interface SynonymsAddChange {
  kind: "synonyms_add";
  id: string;
  label: string;
  terms: string[];
}

export type FieldChange =
  | PreferredTermChange
  | PropertyChange
  | BindingChange
  | SynonymAmendChange
  | SynonymRetireChange
  | SynonymsAddChange;

export interface SendResult {
  rowVersion: number;
  warnings?: DesignationWarning[];
  /** The server's count, present only for a preferred term. */
  length?: number;
  /** The term as stored, which is the cleaned form of what was sent. */
  savedTerm?: string;
}

export type SendChange = (change: FieldChange, rowVersion: number) => Promise<SendResult>;

export type FieldOutcome =
  | {
      status: "saved";
      change: FieldChange;
      warnings: DesignationWarning[];
      length: number | null;
      savedTerm: string | null;
    }
  /** The server refused this field and changed nothing. The run went on. */
  | { status: "refused"; change: FieldChange; error: unknown; message: string }
  /** The failure that ended the run. */
  | { status: "failed"; change: FieldChange; error: unknown; message: string }
  | { status: "not-sent"; change: FieldChange; message: string };

export interface SaveRun {
  outcomes: FieldOutcome[];
  /** The version the entry holds after the last success. */
  rowVersion: number;
  stopped: boolean;
}

/** Statuses where only the one field is at fault. A 409 counts only when it is not a version conflict. */
const FIELD_STATUSES = new Set([400, 404, 409, 422]);

export function endsRun(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    return true;
  }
  if (asVersionConflict(error) !== null) {
    return true;
  }
  return !FIELD_STATUSES.has(error.status);
}

/** One sentence saying why a change was not saved, with no status code in it. */
export function failureMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "The server could not be reached. Check your connection and save again.";
  }
  if (asVersionConflict(error) !== null) {
    return (
      refusalDetail(error) ??
      "Someone else changed this entry after you opened it. Review the changes, then save again."
    );
  }
  const validation = asPropertyValidationError(error);
  if (validation !== null) {
    return validation.issues.map((issue) => issue.message).join(" ");
  }
  const collision = asCollisionError(error);
  if (collision !== null) {
    const names = collision.collisions.map((item) => item.business_key).join(", ");
    return `This term is already in use on ${names}, once case, spacing and punctuation are ignored.`;
  }
  if (error.status === 401) {
    return "Your session has ended. Sign in again, then save again.";
  }
  const detail = refusalDetail(error);
  if (detail !== null) {
    return detail;
  }
  if (error.status === 403) {
    return "You do not have permission to make this change.";
  }
  if (error.status >= 500) {
    return "The server could not complete the save. Try again, or contact an administrator if it keeps happening.";
  }
  return "This could not be saved. Check the value and try again.";
}

export async function runSave(
  changes: FieldChange[],
  send: SendChange,
  startVersion: number,
): Promise<SaveRun> {
  const outcomes: FieldOutcome[] = [];
  let rowVersion = startVersion;
  let stoppedBy: string | null = null;

  for (const change of changes) {
    if (stoppedBy !== null) {
      outcomes.push({
        status: "not-sent",
        change,
        message: `Not sent, because an earlier field failed: ${stoppedBy}`,
      });
      continue;
    }
    try {
      const result = await send(change, rowVersion);
      rowVersion = result.rowVersion;
      outcomes.push({
        status: "saved",
        change,
        warnings: result.warnings ?? [],
        length: result.length ?? null,
        savedTerm: result.savedTerm ?? null,
      });
    } catch (error) {
      const message = failureMessage(error);
      if (endsRun(error)) {
        stoppedBy = message;
        outcomes.push({ status: "failed", change, error, message });
      } else {
        outcomes.push({ status: "refused", change, error, message });
      }
    }
  }

  return { outcomes, rowVersion, stopped: stoppedBy !== null };
}

/**
 * One announcement for the whole run, built from the same outcomes the
 * on-screen summary lists, so the spoken and written accounts cannot differ.
 */
export function describeRun(run: SaveRun): string {
  const saved = run.outcomes.filter((outcome) => outcome.status === "saved");
  const notSaved = run.outcomes.filter((outcome) => outcome.status !== "saved");
  const parts: string[] = [];
  if (saved.length > 0) {
    parts.push(`Saved: ${saved.map((outcome) => outcome.change.label).join(", ")}.`);
  }
  if (notSaved.length > 0) {
    parts.push(
      `Not saved: ${notSaved
        .map((outcome) => `${outcome.change.label}. ${outcome.message}`)
        .join(" ")}`,
    );
  }
  const warnings = saved.reduce(
    (count, outcome) =>
      count + (outcome.status === "saved" ? outcome.warnings.length : 0),
    0,
  );
  if (warnings > 0) {
    parts.push(`${warnings} ${warnings === 1 ? "warning" : "warnings"} to review.`);
  }
  return parts.join(" ");
}
