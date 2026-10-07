import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useAuditEvents, useExportAuditEvents } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import {
  AUDIT_FIELD_IDS,
  AUDIT_FILTER_KEYS,
  FILTER_LABELS,
  auditFilterQuery,
  filterValues,
  formatEventTime,
  validateAuditFilters,
} from "../audit/audit-filters.ts";
import type { AuditFilterKey, AuditFilterValues } from "../audit/audit-filters.ts";
import { saveBlob } from "../audit/save-blob.ts";
import { Button } from "../components/button.tsx";
import { Card } from "../components/card.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Field } from "../components/field.tsx";
import { FilterBar } from "../components/filter-bar.tsx";
import { Form } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Pagination } from "../components/pagination.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The audit log screen (NFR-12, NFR-09, NFR-31).
 *
 * Reads `GET /audit/events`, newest first, and exports the same filtered set
 * through `GET /audit/events/export`. No permission check runs here (NFR-20):
 * a caller without `audit.read` gets the server's 403, and the screen shows its
 * sentence in place of the results.
 *
 * Filters and the page cursor live in the URL, so a pasted link restores the
 * same view. Paging is keyset: no page number and no total exist.
 */

const ROUTE_ID = "/authenticated/admin/audit" as const;
const ROUTE_PATH = "/admin/audit" as const;

type Row = components["schemas"]["AuditEventItem"];

const PAGE_SIZE = 50;
const EXPORT_FILE_NAME = "audit-events.ndjson";

const LOAD_FAILURE =
  "The audit log could not be loaded. Try again in a moment, or contact an administrator if the problem persists.";
const STALE_DATA_WARNING =
  "The audit log could not be refreshed just now, so these events may be out of date.";
const EXPORT_FAILURE =
  "The export could not be prepared. Try again, or contact an administrator if the problem persists.";

function resultAnnouncement(count: number, hasNext: boolean): string {
  if (count === 0) {
    return "No events.";
  }
  const shown = `${count} event${count === 1 ? "" : "s"} on this page.`;
  return hasNext ? `${shown} More events are on the next page.` : shown;
}

function emptyStateText(hasFilters: boolean, hasCursor: boolean): string {
  if (hasFilters) {
    return "No audit events match these filters.";
  }
  return hasCursor ? "No more audit events." : "The audit log has no events yet.";
}

export function AdminAuditPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_PATH });
  const { message, politeness, announce } = useAnnounce();

  const appliedFilters = filterValues(search);
  const appliedErrors = validateAuditFilters(appliedFilters);
  const filtersValid = appliedErrors.length === 0;
  const hasFilters = Object.keys(appliedFilters).length > 0;
  const filterQuery = filtersValid ? auditFilterQuery(appliedFilters) : {};

  const events = useAuditEvents({
    filters: filterQuery,
    limit: PAGE_SIZE,
    before: search.before,
    enabled: filtersValid,
  });
  const exportEvents = useExportAuditEvents();

  // The draft is what the form shows. It follows the URL, so Back, Forward or a
  // pasted link fills the form, yet stays editable until applied.
  const appliedKey = JSON.stringify(appliedFilters);
  const [syncedKey, setSyncedKey] = useState(appliedKey);
  const [draft, setDraft] = useState<AuditFilterValues>(appliedFilters);
  const [attempted, setAttempted] = useState(false);
  if (appliedKey !== syncedKey) {
    setSyncedKey(appliedKey);
    setDraft(appliedFilters);
    setAttempted(false);
  }

  // Errors in the URL show at once; errors in the draft show once the user has
  // tried to apply it, so an empty form never accuses them of anything.
  const shownErrors = attempted ? validateAuditFilters(draft) : appliedErrors;
  const errorFor = (key: AuditFilterKey) =>
    shownErrors.find((error) => error.key === key)?.message;

  // The cursors of pages already visited, so "Previous page" can return to
  // one. The stack survives a change of `before` only when that change is the
  // Next or Previous click that set `target`; anything else empties it.
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

  // Results are shown only for filters that are valid: a disabled query can
  // still hold data from an earlier view, and the table must never sit under a
  // filter error as if it were that filter's answer.
  const data = filtersValid ? events.data : undefined;
  const items: Row[] = data?.items ?? [];
  const nextCursor = data?.next_cursor ?? null;

  const loadFailed = filtersValid && events.isError;
  const hardFailure = loadFailed && data === undefined;
  const staleData = loadFailed && data !== undefined;
  const hardFailureMessage = hardFailure
    ? (refusalDetail(events.error) ?? LOAD_FAILURE)
    : null;
  // An export failure belongs to the filters it ran with. Once the applied
  // filters differ, the message would describe a request the screen no longer
  // shows, so it is not shown.
  const exportMatchesFilters =
    filtersValid &&
    JSON.stringify(exportEvents.variables) === JSON.stringify(filterQuery);
  const exportFailureMessage =
    exportEvents.isError && exportMatchesFilters
      ? (refusalDetail(exportEvents.error) ?? EXPORT_FAILURE)
      : null;
  const errorSummaryText = appliedErrors.map((error) => error.message).join(" ");

  // The message, not the error, is the dependency: a refetch that fails the
  // same way yields a new error object with unchanged wording, which would
  // otherwise be announced twice.
  useEffect(() => {
    if (hardFailureMessage !== null) {
      announce(hardFailureMessage);
    }
  }, [hardFailureMessage, announce]);

  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  useEffect(() => {
    if (exportFailureMessage !== null) {
      announce(exportFailureMessage);
    }
  }, [exportFailureMessage, announce]);

  useEffect(() => {
    if (errorSummaryText.length > 0) {
      announce(`The filters need fixing. ${errorSummaryText}`);
    }
  }, [errorSummaryText, announce]);

  // `data` keeps its identity across a refetch that returns the same page, so
  // this speaks once per new result set. A placeholder is the page being
  // replaced, and is never announced.
  const resultMessage =
    data && !events.isError && !events.isPlaceholderData
      ? resultAnnouncement(data.items.length, data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(resultMessage);
    }
  }, [data, resultMessage, announce]);

  function applyFilters(values: AuditFilterValues) {
    void navigate({ search: () => values });
  }

  function handleSubmit() {
    setAttempted(true);
    const values = filterValues(draft);
    if (validateAuditFilters(values).length > 0) {
      return;
    }
    applyFilters(values);
    return Promise.resolve({ ok: true });
  }

  function handleRemoveFilter(key: AuditFilterKey) {
    const next = { ...appliedFilters };
    delete next[key];
    if (key === "entity_type") {
      delete next.entity_id;
    }
    applyFilters(next);
  }

  function handleFilterByActor(row: Row) {
    if (row.actor === null) {
      return;
    }
    applyFilters({ ...appliedFilters, actor: row.actor.id });
    announce(`Filtering by actor ${row.actor.display_name ?? row.actor.id}.`);
  }

  function handleNextPage() {
    // A placeholder's `next_cursor` points at the page already loading, so
    // acting on it would push a duplicate onto the Previous stack.
    if (nextCursor !== null && !events.isPlaceholderData) {
      setPaging({
        ...paging,
        stack: [...paging.stack, search.before],
        target: { before: nextCursor },
      });
      void navigate({ search: (prev) => ({ ...prev, before: nextCursor }) });
    }
  }

  function handlePreviousPage() {
    const previousBefore = paging.stack[paging.stack.length - 1];
    setPaging({
      ...paging,
      stack: paging.stack.slice(0, -1),
      target: { before: previousBefore },
    });
    void navigate({ search: (prev) => ({ ...prev, before: previousBefore }) });
  }

  function handleExport() {
    if (exportEvents.isPending || !filtersValid) {
      return;
    }
    announce("Preparing the export.");
    exportEvents.mutate(auditFilterQuery(appliedFilters), {
      onSuccess: (blob) => {
        saveBlob(blob, EXPORT_FILE_NAME);
        announce(`The export is ready. Your browser saves it as ${EXPORT_FILE_NAME}.`);
      },
    });
  }

  function setDraftValue(key: AuditFilterKey, value: string) {
    setDraft((previous) => ({ ...previous, [key]: value }));
  }

  const entityIdEnabled = Boolean(draft.entity_type?.trim() || draft.entity_id?.trim());

  const exportButton = (
    <Button
      type="button"
      variant="secondary"
      aria-disabled={exportEvents.isPending || !filtersValid}
      onClick={handleExport}
    >
      {exportEvents.isPending ? "Preparing export…" : "Export as NDJSON"}
    </Button>
  );

  const textField = (key: AuditFilterKey, hint: string) => (
    <Field
      id={AUDIT_FIELD_IDS[key]}
      label={FILTER_LABELS[key]}
      hint={hint}
      error={errorFor(key)}
    >
      {(control) => (
        <input
          {...control}
          type="text"
          value={draft[key] ?? ""}
          onChange={(event) => setDraftValue(key, event.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={INPUT_CLASSES}
        />
      )}
    </Field>
  );

  const dateField = (key: "from" | "to") => (
    <Field id={AUDIT_FIELD_IDS[key]} label={FILTER_LABELS[key]} error={errorFor(key)}>
      {(control) => (
        <input
          {...control}
          type="date"
          value={draft[key] ?? ""}
          onChange={(event) => setDraftValue(key, event.target.value)}
          className={INPUT_CLASSES}
        />
      )}
    </Field>
  );

  const emptyState = (
    <div className="flex flex-col items-start gap-2">
      <p className="m-0">{emptyStateText(hasFilters, search.before !== undefined)}</p>
      {hasFilters ? (
        <Button type="button" variant="secondary" onClick={() => applyFilters({})}>
          Clear all filters
        </Button>
      ) : null}
    </div>
  );

  return (
    <section aria-labelledby="audit-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="flex flex-col gap-4 py-6">
        <PageHeader
          id="audit-heading"
          title="Audit log"
          meta="Newest events first. The export holds every event that matches the applied filters, oldest first."
          actions={hardFailure ? undefined : exportButton}
        />

        {exportFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{exportFailureMessage}</p>
        )}

        {/* Outside the results gate: changing a filter is the way out of a
            refused or failed request, so the form stays reachable. */}
        <Card>
          <Form
            aria-label="Filter the audit log"
            onSubmit={handleSubmit}
            errors={shownErrors.map((error) => ({
              fieldId: AUDIT_FIELD_IDS[error.key],
              message: error.message,
            }))}
            errorSummaryTitle="Some filters need fixing"
            submitLabel="Apply filters"
            secondaryActions={
              hasFilters ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => applyFilters({})}
                >
                  Clear all filters
                </Button>
              ) : undefined
            }
          >
            <div className="grid grid-cols-[repeat(auto-fit,minmax(16rem,1fr))] gap-4">
              {textField(
                "actor",
                "A user id. Select an actor's name in the results to fill it in.",
              )}
              {textField("entity_type", "For example catalogue_entry.")}
              <Field
                id={AUDIT_FIELD_IDS.entity_id}
                label={FILTER_LABELS.entity_id}
                hint={
                  entityIdEnabled
                    ? "The entity's own identifier."
                    : "Enter an entity type first."
                }
                error={errorFor("entity_id")}
              >
                {(control) => (
                  <input
                    {...control}
                    type="text"
                    value={draft.entity_id ?? ""}
                    disabled={!entityIdEnabled}
                    onChange={(event) => setDraftValue("entity_id", event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    className={INPUT_CLASSES}
                  />
                )}
              </Field>
              {textField("action", "For example catalogue_entry.updated.")}
            </div>
            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend className="mb-1 p-0 text-sm font-medium text-[var(--color-text)]">
                Date range, in Australian Eastern Standard Time (UTC+10)
              </legend>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(12rem,1fr))] gap-4">
                {dateField("from")}
                {dateField("to")}
              </div>
              <p className="m-0 text-sm text-[var(--color-text-muted)]">
                Both days are included.
              </p>
            </fieldset>
          </Form>
        </Card>

        <FilterBar
          activeFilters={AUDIT_FILTER_KEYS.flatMap((key) => {
            const value = appliedFilters[key];
            return value === undefined
              ? []
              : [{ key, facetLabel: FILTER_LABELS[key], valueLabel: value }];
          })}
          onRemove={(key) => handleRemoveFilter(key as AuditFilterKey)}
          onClearAll={() => applyFilters({})}
        />

        {filtersValid && events.isPending && <p className="m-0">Loading audit events…</p>}

        {hardFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p className="m-0">{STALE_DATA_WARNING}</p>}

        {data && (
          <>
            <div className="overflow-x-auto">
              <DataTable
                caption="Audit events"
                columns={[
                  {
                    key: "occurred_at",
                    header: "When (UTC+10)",
                    isRowHeader: true,
                    render: (row: Row) => (
                      <time dateTime={row.occurred_at} className="whitespace-nowrap">
                        {formatEventTime(row.occurred_at)}
                      </time>
                    ),
                  },
                  {
                    key: "actor",
                    header: "Actor",
                    render: (row: Row) => {
                      if (row.actor === null) {
                        return (
                          <span className="text-[var(--color-text-muted)]">System</span>
                        );
                      }
                      const name = row.actor.display_name ?? "Closed account";
                      return (
                        <button
                          type="button"
                          aria-label={`Filter by actor ${name}`}
                          onClick={() => handleFilterByActor(row)}
                          className="inline-flex min-h-6 cursor-pointer items-center border-0 bg-transparent p-0 text-left text-[var(--color-accent)] hover:underline"
                        >
                          {name}
                        </button>
                      );
                    },
                  },
                  {
                    key: "action",
                    header: "Action",
                    render: (row: Row) => <span className="font-mono">{row.action}</span>,
                  },
                  {
                    key: "entity",
                    header: "Entity",
                    render: (row: Row) => (
                      <span className="font-mono">
                        {row.entity_type} {row.entity_id}
                      </span>
                    ),
                  },
                  {
                    key: "reason",
                    header: "Reason",
                    render: (row: Row) =>
                      row.reason === null || row.reason === "" ? (
                        <span className="text-[var(--color-text-muted)]">
                          None recorded
                        </span>
                      ) : (
                        row.reason
                      ),
                  },
                ]}
                rows={items}
                getRowKey={(row) => String(row.sequence)}
                emptyState={emptyState}
              />
            </div>

            {(items.length > 0 || paging.stack.length > 0) && (
              <Pagination
                hasNext={nextCursor !== null}
                onNext={handleNextPage}
                onPrevious={paging.stack.length > 0 ? handlePreviousPage : undefined}
                className="self-start"
              />
            )}
          </>
        )}
      </PageContainer>
    </section>
  );
}
