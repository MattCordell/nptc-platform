import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { useEntryHistory } from "../api/queries.ts";
import { ApiError } from "../api/unwrap.ts";
import { EntryAuditTrail } from "../catalogue/entry-audit-trail.tsx";
import { Breadcrumb } from "../components/breadcrumb.tsx";
import { Button } from "../components/button.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Pagination } from "../components/pagination.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { NotFoundPage } from "../shell/not-found-page.tsx";
import { useDocumentTitle } from "../shell/use-document-title.ts";

/**
 * One entry's full change history, read anonymously (FR-19, NFR-31).
 *
 * The cursor lives in the URL, so a pasted link opens the same page. Paging is
 * keyset: the API sends no previous cursor and no total, so the page remembers
 * the cursors it has left to offer "Previous page".
 *
 * The API answers a key that does not exist and a key that is not public with
 * the same 404, and a malformed key or cursor with a 422 whose body is not a
 * declared shape. Both show the not-found page, and the body is never read.
 */

const ROUTE_ID = "/catalogue/$businessKey/history" as const;

const HEADING_ID = "entry-history-heading";
const PAGE_SIZE = 50;

const LOAD_FAILURE = "The change history could not be loaded. Try again in a moment.";
const STALE_DATA_WARNING =
  "The change history could not be refreshed just now, so it may be out of date.";

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

function resultAnnouncement(count: number, hasNext: boolean): string {
  if (count === 0) {
    return "No changes on this page.";
  }
  const shown = `${count} change${count === 1 ? "" : "s"} on this page.`;
  return hasNext ? `${shown} More changes are on the next page.` : shown;
}

export function EntryHistoryPage() {
  const { businessKey } = useParams({ from: ROUTE_ID });
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_ID });
  const history = useEntryHistory(businessKey, {
    limit: PAGE_SIZE,
    before: search.before,
    keepPreviousPage: true,
  });
  const { message, politeness, announce } = useAnnounce();
  useDocumentTitle(`Change history for ${businessKey} — NPTC Catalogue`);

  // The cursors of pages already visited, so "Previous page" can return to
  // one. The stack survives a change of `before` only when that change is the
  // Next or Previous click that set `target`; any other change empties it.
  const [paging, setPaging] = useState<{
    stack: (string | undefined)[];
    seen: string | undefined;
    target: { before: string | undefined } | null;
  }>({ stack: [], seen: search.before, target: null });
  if (search.before !== paging.seen) {
    setPaging({
      stack:
        paging.target !== null && paging.target.before === search.before
          ? paging.stack
          : [],
      seen: search.before,
      target: null,
    });
  }

  const data = history.data;
  const items = data?.items ?? [];
  const nextCursor = data?.next_cursor ?? null;
  const hardFailure = history.isError && data === undefined && !isNotFound(history.error);
  const staleData = history.isError && data !== undefined;

  useEffect(() => {
    if (hardFailure) {
      announce(LOAD_FAILURE);
    }
  }, [hardFailure, announce]);

  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  // `data` keeps its identity across a refetch that returns the same page, so
  // this speaks once per new result set. A placeholder is the page being
  // replaced, and is never announced.
  const resultMessage =
    data && !history.isError && !history.isPlaceholderData
      ? resultAnnouncement(data.items.length, data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(resultMessage);
    }
  }, [data, resultMessage, announce]);

  if (isNotFound(history.error)) {
    return <NotFoundPage />;
  }

  function handleNextPage() {
    // A placeholder's `next_cursor` points at the page already loading, so
    // acting on it would push a duplicate onto the Previous stack.
    if (nextCursor !== null && !history.isPlaceholderData) {
      setPaging({
        ...paging,
        stack: [...paging.stack, search.before],
        target: { before: nextCursor },
      });
      void navigate({ search: { before: nextCursor } });
    }
  }

  function handlePreviousPage() {
    const previousBefore = paging.stack[paging.stack.length - 1];
    setPaging({
      ...paging,
      stack: paging.stack.slice(0, -1),
      target: { before: previousBefore },
    });
    void navigate({ search: { before: previousBefore } });
  }

  const emptyText =
    search.before === undefined
      ? "No changes are recorded for this entry."
      : "No more changes are recorded.";

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="flex flex-col gap-4 py-6">
        <Breadcrumb
          ancestors={[
            <Link key="home" to="/">
              Home
            </Link>,
            <Link key="catalogue" to="/catalogue">
              Catalogue
            </Link>,
            <Link key="entry" to="/catalogue/$businessKey" params={{ businessKey }}>
              {businessKey}
            </Link>,
          ]}
          current="Change history"
        />

        <PageHeader
          id={HEADING_ID}
          title={`Change history for ${businessKey}`}
          meta="Newest changes first. Times are in Australian Eastern Standard Time (UTC+10)."
        />

        {staleData ? <p className="m-0">{STALE_DATA_WARNING}</p> : null}

        {history.isPending ? (
          <p className="m-0 text-[var(--color-text-muted)]">
            Loading the change history…
          </p>
        ) : hardFailure ? (
          <div className="flex flex-col items-start gap-3">
            <p className="m-0 text-[var(--color-danger)]">{LOAD_FAILURE}</p>
            <Button
              type="button"
              variant="secondary"
              onClick={() => void history.refetch()}
            >
              Try again
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p className="m-0 text-[var(--color-text-muted)]">{emptyText}</p>
        ) : (
          <EntryAuditTrail events={items} />
        )}

        {data !== undefined && (items.length > 0 || paging.stack.length > 0) ? (
          <Pagination
            hasNext={nextCursor !== null}
            onNext={handleNextPage}
            onPrevious={paging.stack.length > 0 ? handlePreviousPage : undefined}
            className="self-start"
          />
        ) : null}
      </PageContainer>
    </section>
  );
}
