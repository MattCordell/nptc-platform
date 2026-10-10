import type { QuotaBody } from "../api/conflicts.ts";

/**
 * What to tell a submitter whose quota is used up (FR-43). The three limits
 * need three different answers, because only some of them lift with time:
 * saying "try again later" about a lifetime limit would send the user round a
 * loop that never ends.
 */

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** "in 12 seconds", "in about 14 minutes" or "in about 2 hours", rounded up so it is never too early. */
export function waitPhrase(seconds: number): string {
  if (seconds < 60) {
    return `in ${plural(seconds, "second")}`;
  }
  const minutes = Math.ceil(seconds / 60);
  if (minutes <= 60) {
    return `in about ${plural(minutes, "minute")}`;
  }
  return `in about ${plural(Math.ceil(minutes / 60), "hour")}`;
}

export function quotaMessage(body: QuotaBody, retryAfter: number | null): string {
  switch (body.limit) {
    case "hourly":
      return `You have reached the limit of ${plural(body.maximum, "submission")} in one hour. ${
        retryAfter === null
          ? "You can submit again later."
          : `You can submit again ${waitPhrase(retryAfter)}.`
      } Nothing was submitted.`;
    case "lifetime":
      return `You have used all ${plural(body.maximum, "submission")} your account can make. Waiting will not lift this limit. Contact an administrator if you need to submit more. Nothing was submitted.`;
    case "concurrent":
      return `An earlier submission from you is still being processed. Try again ${
        retryAfter === null ? "in a few seconds" : waitPhrase(retryAfter)
      }. Nothing was submitted.`;
  }
}
