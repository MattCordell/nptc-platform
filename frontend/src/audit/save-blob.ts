/**
 * Saves `blob` as a file named `filename`. A plain link cannot do this for an
 * audit export: the bearer token is not a cookie, so a link would arrive
 * unauthenticated.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
