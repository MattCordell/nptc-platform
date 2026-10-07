const REVOKE_DELAY_MS = 10_000;

/**
 * Saves `blob` as a file named `filename`. A plain link cannot do this for an
 * audit export: the bearer token is not a cookie, so a link would arrive
 * unauthenticated. The object URL outlives the click, because some browsers
 * start the download after the click task ends and cancel it if the URL is
 * already revoked.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}
