import { useParams } from "@tanstack/react-router";

import { refusalDetail } from "../api/conflicts.ts";
import { useAdminEntryDetail } from "../api/queries.ts";
import { ApiError } from "../api/unwrap.ts";
import { DesignationsPanel } from "../catalogue/designations-panel.tsx";

/**
 * The catalogue entry editing screen (issue #149; FR-36).
 *
 * Loads through the *admin* detail route, not the public one: an entry being
 * edited is very often `draft`, and the public route 404s a draft entry
 * identically to a key that was never minted (#142/#228).
 *
 * This page is the shell. #149 fills in the designations panel; #150 (code
 * bindings) and #151 (registry properties) add sibling sections below it.
 */

export function AdminCatalogueEditPage() {
  const { businessKey } = useParams({
    from: "/authenticated/admin/catalogue/$businessKey/edit",
  });
  const entry = useAdminEntryDetail(businessKey);

  return (
    <section aria-labelledby="edit-entry-heading">
      <h1 id="edit-entry-heading">
        {entry.data ? entry.data.preferred_term : `Edit ${businessKey}`}
      </h1>

      {entry.isPending && <p>Loading {businessKey}…</p>}

      {entry.isError && <LoadFailure businessKey={businessKey} error={entry.error} />}

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

          <DesignationsPanel entry={entry.data} />
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
