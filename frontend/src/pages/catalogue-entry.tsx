import { Link, useParams } from "@tanstack/react-router";
import { useEffect } from "react";

import { useEntryDetail } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import { useAuthStatus } from "../auth/auth-status.ts";
import { EntryBindings } from "../catalogue/entry-bindings.tsx";
import { EntryHistoryList } from "../catalogue/entry-history-list.tsx";
import { EntryMetadata } from "../catalogue/entry-metadata.tsx";
import { EntryTerms } from "../catalogue/entry-terms.tsx";
import { statusLabelFor, statusToneFor } from "../catalogue/status-options.ts";
import { Breadcrumb } from "../components/breadcrumb.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { Button } from "../components/button.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import { DetailLayout } from "../components/detail-layout.tsx";
import { FindingIndicator } from "../components/finding-indicator.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { NotFoundPage } from "../shell/not-found-page.tsx";
import { useDocumentTitle } from "../shell/use-document-title.ts";

/**
 * One published catalogue entry, read anonymously (FR-17, FR-18, FR-20,
 * NFR-31): its terms, and any retired SNOMED CT codes, beside its details
 * (properties included) and recent changes.
 *
 * A signed-in reader of an active entry also sees a "Propose a change" link
 * (FR-35). It checks sign-in and status and no permission (NFR-20): the form
 * it opens shows the server's refusal to a user who may not propose.
 *
 * The API answers a key that does not exist and a key that is not public with
 * the same 404, so the page cannot tell them apart and does not try. A key that
 * is not well formed gets a 422 whose body is not a declared shape, so the page
 * never reads it. Both show the not-found page.
 */

type EntryDetail = components["schemas"]["EntryDetail"];

const HEADING_ID = "catalogue-entry-heading";

const STALE_DATA_WARNING =
  "This entry could not be refreshed just now, so it may be out of date.";

const LOAD_FAILURE = "This entry could not be loaded. Try again in a moment.";

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

function EntryBreadcrumb({ current }: { current: string }) {
  return (
    <Breadcrumb
      ancestors={[
        <Link key="home" to="/">
          Home
        </Link>,
        <Link key="catalogue" to="/catalogue">
          Catalogue
        </Link>,
      ]}
      current={current}
    />
  );
}

function EntryView({
  entry,
  refreshFailed,
}: {
  entry: EntryDetail;
  refreshFailed: boolean;
}) {
  useDocumentTitle(`${entry.preferred_term} — NPTC Catalogue`);
  const { message, politeness, announce } = useAnnounce();
  const canPropose = useAuthStatus() === "signed-in" && entry.status === "active";

  useEffect(() => {
    if (refreshFailed) {
      announce(STALE_DATA_WARNING);
    }
  }, [refreshFailed, announce]);

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="py-6">
        <EntryBreadcrumb current={entry.preferred_term} />

        <PageHeader
          id={HEADING_ID}
          title={entry.preferred_term}
          meta={
            <span className="flex flex-wrap items-center gap-2">
              {entry.code === null ? (
                <span>No SNOMED CT code</span>
              ) : (
                <CodeChip code={entry.code} />
              )}
              <StatusBadge
                tone={statusToneFor(entry.status)}
                label={statusLabelFor(entry.status)}
              />
              {entry.has_open_finding ? <FindingIndicator /> : null}
            </span>
          }
          actions={
            canPropose ? (
              <Link
                to="/submissions/new"
                search={{ entry: entry.business_key }}
                className={buttonClassName("secondary")}
              >
                Propose a change
              </Link>
            ) : null
          }
        />

        {refreshFailed ? <p className="m-0">{STALE_DATA_WARNING}</p> : null}

        <DetailLayout
          sidebarLabel="Entry details"
          sidebar={
            <>
              <EntryMetadata entry={entry} />
              <EntryHistoryList businessKey={entry.business_key} />
            </>
          }
        >
          <EntryTerms entry={entry} />
          <EntryBindings bindings={entry.bindings} />
        </DetailLayout>
      </PageContainer>
    </section>
  );
}

export function CatalogueEntryPage() {
  const { businessKey } = useParams({ from: "/catalogue/$businessKey/" });
  const entry = useEntryDetail(businessKey);
  const { message, politeness, announce } = useAnnounce();

  const hardFailure =
    entry.isError && entry.data === undefined && !isNotFound(entry.error);
  useEffect(() => {
    if (hardFailure) {
      announce(LOAD_FAILURE);
    }
  }, [hardFailure, announce]);

  // An entry that stopped being public between two loads is not found either,
  // whatever is still cached.
  if (isNotFound(entry.error)) {
    return <NotFoundPage />;
  }

  if (entry.data !== undefined) {
    return <EntryView entry={entry.data} refreshFailed={entry.isError} />;
  }

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="py-6">
        <EntryBreadcrumb current={businessKey} />
        <PageHeader id={HEADING_ID} title={businessKey} />
        {hardFailure ? (
          <div className="flex flex-col items-start gap-3">
            <p className="m-0 text-[var(--color-danger)]">{LOAD_FAILURE}</p>
            <Button
              type="button"
              variant="secondary"
              onClick={() => void entry.refetch()}
            >
              Try again
            </Button>
          </div>
        ) : (
          <p className="m-0">Loading entry…</p>
        )}
      </PageContainer>
    </section>
  );
}
