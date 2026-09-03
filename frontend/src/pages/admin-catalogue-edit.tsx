import { useParams } from "@tanstack/react-router";
import { useEffect } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useAdminEntryDetail } from "../api/queries.ts";
import { ApiError } from "../api/unwrap.ts";
import { BindingsPanel } from "../catalogue/bindings-panel.tsx";
import { DesignationsPanel } from "../catalogue/designations-panel.tsx";
import { PropertiesPanel } from "../catalogue/properties-panel.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The catalogue entry editing screen (issue #149; FR-36).
 *
 * Loads through the *admin* detail route, not the public one: an entry being
 * edited is very often `draft`, and the public route 404s a draft entry
 * identically to a key that was never minted (#142/#228).
 *
 * This page is the shell. #149 fills in the designations panel, #150 the code
 * bindings panel, and #151 the registry properties panel below it - the last
 * of the three sibling sections.
 */

/**
 * A refresh that failed over an entry already on screen. One string, because
 * it is both shown and announced and the two must not drift.
 */
function staleWarning(businessKey: string): string {
  return (
    `${businessKey} could not be refreshed just now, so what follows may be out of ` +
    "date. Sign in again, or reload the page, before making further changes."
  );
}

export function AdminCatalogueEditPage() {
  const { businessKey } = useParams({
    from: "/authenticated/admin/catalogue/$businessKey/edit",
  });
  const entry = useAdminEntryDetail(businessKey);
  const { message, politeness, announce } = useAnnounce();

  // `isError` with data is a *refresh* that failed, which is announced rather
  // than rendered into a live region as it appears: `LiveRegion` is mounted
  // and empty from the first render for the reason its own docstring gives -
  // a screen reader reliably reports a text change inside a region that was
  // already there, and not an element created and filled in the same tick
  // (#148). This banner's whole job is telling an editor that what they are
  // reading may be stale, so the reader who cannot see it appear is the one
  // who most needs it (PR #238 review).
  const staleData = entry.isError && entry.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(staleWarning(businessKey));
    }
  }, [staleData, businessKey, announce]);

  return (
    <section aria-labelledby="edit-entry-heading">
      <LiveRegion message={message} politeness={politeness} />

      <h1 id="edit-entry-heading">
        {entry.data ? entry.data.preferred_term : `Edit ${businessKey}`}
      </h1>

      {entry.isPending && <p>Loading {businessKey}…</p>}

      {/* `isError` and `data` are not exclusive: `retry` is off and
          `refetchOnWindowFocus` is on, so an entry that loaded and then failed
          a *re*fetch has both. Rendering the failure paragraph unconditionally
          put "you cannot edit this entry" directly above a working editor -
          and the amend mutation's conflict refetch makes that a designed-in
          path, since a long-open session is exactly when one expires (PR #238
          review). A first load that fails blocks; a failed refresh is a
          banner over the terms the screen already has. */}
      {entry.isError && entry.data === undefined && (
        <LoadFailure businessKey={businessKey} error={entry.error} />
      )}

      {staleData && <p>{staleWarning(businessKey)}</p>}

      {entry.data && (
        <>
          <dl>
            <dt>Identifier</dt>
            <dd>{entry.data.business_key}</dd>
            <dt>Entry status</dt>
            <dd>{entry.data.status}</dd>
            {/* FR-85/FR-24: the published character count of the preferred
                term, computed by the server and shown as text. There is
                deliberately no control for it anywhere on this screen. */}
            <dt>Preferred term length</dt>
            <dd>{entry.data.length}</dd>
            <dt>Last changed</dt>
            <dd>{new Date(entry.data.updated_at).toLocaleString()}</dd>
          </dl>

          {/* Keyed on the entry so its panel state - the warnings from the
              last write, and any open dialog - cannot survive navigation from
              one entry's edit screen to another's, which re-renders this same
              route component rather than remounting it (review finding 4). */}
          <DesignationsPanel key={entry.data.business_key} entry={entry.data} />
          <BindingsPanel key={entry.data.business_key} entry={entry.data} />
          <PropertiesPanel key={entry.data.business_key} entry={entry.data} />
        </>
      )}
    </section>
  );
}

/**
 * A failed load, in the terms the reader can act on.
 *
 * 403 is called out separately because it is the likely one here and the
 * generic sentence would mislead: `catalogue.edit_published` requires
 * multi-factor authentication, and the SPA does not yet answer the step-up
 * challenge the API sends with that refusal (issue #184). Until it does, an
 * administrator signed in without MFA lands here, and telling them to sign in
 * again is the only advice that actually works.
 */
function LoadFailure({ businessKey, error }: { businessKey: string; error: unknown }) {
  const status = error instanceof ApiError ? error.status : null;

  if (status === 404) {
    return <p>No catalogue entry was found for {businessKey}. Check the identifier.</p>;
  }
  if (status === 401 || status === 403) {
    return (
      <p>
        You cannot edit this entry with your current sign-in. Editing the catalogue
        requires an administrator account with multi-factor authentication. Sign out and
        sign in again, completing the second step, then try once more.
      </p>
    );
  }
  return (
    <p>
      {refusalDetail(error) ??
        `${businessKey} could not be loaded. Try again, or contact an administrator if the problem persists.`}
    </p>
  );
}
