/**
 * A timestamp as a date in the reader's own time zone, e.g. "1 September 2026".
 * An unparseable value is returned as given rather than shown as "Invalid Date".
 */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleDateString("en-AU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}
