import { useEffect, useRef, type ReactNode } from "react";

import { useAcceptTerms, useCurrentTerms } from "../api/queries.ts";
import { TermsAcceptance } from "./terms-acceptance.tsx";

/**
 * Puts the acceptance gate in front of the signed-in routes (NFR-45,
 * ADR-0043). It presents only: the server refuses each contribution until the
 * current terms are accepted, so a failed or slow read of the terms never
 * blocks the app. The gate shows when the API says `accepted: false`, and a
 * 403 `terms_acceptance_required` from any request invalidates that read so a
 * mid-session change shows the gate too (`createQueryClient`).
 *
 * The page stays mounted, hidden, while the gate shows. A write refused
 * mid-edit therefore does not throw away what the user typed; they accept and
 * return to the same screen. While the terms are still loading the page shows
 * as normal, so a user who has accepted never waits on this read.
 */
export function TermsGate({ children }: { children: ReactNode }) {
  const terms = useCurrentTerms();
  const accept = useAcceptTerms();
  const current = terms.data;
  const gated = current !== undefined && !current.accepted;

  // A removed gate takes keyboard focus with it. Hand it to the page, as a
  // navigation does, so the user is not left at the top of the document.
  const wasGated = useRef(false);
  useEffect(() => {
    if (wasGated.current && !gated) {
      document.getElementById("main-content")?.focus();
    }
    wasGated.current = gated;
  }, [gated]);

  return (
    <>
      {gated ? (
        <TermsAcceptance
          key={current.version}
          terms={current}
          pending={accept.isPending}
          error={accept.error}
          onAccept={(version) =>
            accept.mutateAsync(version).then(
              () => ({ ok: true }),
              () => ({ ok: false }),
            )
          }
        />
      ) : null}
      <div hidden={gated}>{children}</div>
    </>
  );
}
