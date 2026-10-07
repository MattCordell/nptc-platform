import { asVersionConflict, refusalDetail } from "../api/conflicts.ts";
import { formatValue } from "../catalogue/collision-notice.tsx";

/**
 * What a refused registry write shows in `Form`'s error slot. A stale-version
 * 409 gets its own wording because the write hooks refetch the property on it;
 * every other refusal shows the server's own sentence, or `fallback` when the
 * body carries none (a FastAPI validation array, or an empty body).
 */
export function PropertyRefusalNotice({
  error,
  fallback,
  editsKept = false,
}: {
  error: unknown;
  fallback: string;
  /** True on the edit form, which moves untouched fields to the reloaded values. */
  editsKept?: boolean;
}) {
  const conflict = asVersionConflict(error);
  if (conflict === null) {
    return <p>{refusalDetail(error) ?? fallback}</p>;
  }
  const changedAt = conflict.changed_at === null ? null : new Date(conflict.changed_at);
  return (
    <div>
      <p>
        Someone else changed this property while you had it open
        {conflict.changed_by === null ? "" : `, most recently ${conflict.changed_by}`}
        {changedAt === null ? "" : ` at ${changedAt.toLocaleString()}`}.
      </p>
      {conflict.conflicts.length > 0 && (
        <ul>
          {conflict.conflicts.map((item) => (
            <li key={item.field}>
              <strong>{item.field}</strong>: you sent {formatValue(item.submitted)}; it is
              now {formatValue(item.current)}
            </li>
          ))}
        </ul>
      )}
      <p>Nothing has been saved.</p>
      <p>
        The property is reloading with their change.
        {editsKept
          ? " Fields you have not edited will show the new values, and your edits stay."
          : ""}{" "}
        Check yours is still needed, then save it again.
      </p>
    </div>
  );
}
