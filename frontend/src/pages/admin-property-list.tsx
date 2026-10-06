import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect } from "react";
import type { ChangeEvent } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { usePropertyDefinitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { scopeLabelFor } from "../catalogue/property-display.ts";
import { statusLabelFor, statusToneFor } from "../catalogue/status-options.ts";
import { Checkbox } from "../components/checkbox.tsx";
import { DataTable } from "../components/data-table.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The property registry list screen (FR-08..13, NFR-31).
 *
 * Reads the one unpaged `GET /registry/properties` response, which always
 * includes deprecated properties, and hides them in the browser unless the URL
 * carries `deprecated=show`. Filtering here keeps a single cache entry shared
 * with the catalogue screens, and a pasted link restores the same view.
 */

// `useSearch` is keyed by the route id, which includes the pathless
// `authenticated` segment; `useNavigate` is keyed by the URL path.
const ROUTE_ID = "/authenticated/admin/properties/" as const;
const ROUTE_PATH = "/admin/properties/" as const;

type Row = components["schemas"]["PropertyDefinitionResponse"];

const HARD_FAILURE_MESSAGE =
  "The property registry could not be loaded. Try again, or contact an administrator if the problem persists.";

const STALE_DATA_WARNING =
  "The property registry could not be refreshed just now, so what follows may be out of date. Reload the page to try again.";

function isDeprecated(row: Row): boolean {
  return row.status === "deprecated";
}

function propertyCount(count: number): string {
  return `${count} ${count === 1 ? "property" : "properties"}`;
}

function summaryText(rows: Row[]): string {
  const deprecated = rows.filter(isDeprecated).length;
  return `${propertyCount(rows.length)}: ${rows.length - deprecated} active, ${deprecated} deprecated`;
}

function emptyStateText(total: number): string {
  return total === 0
    ? "No properties are defined in the registry yet."
    : "Every property is deprecated. Show deprecated properties to see them.";
}

export function AdminPropertyListPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_PATH });
  const definitions = usePropertyDefinitions();
  const { message, politeness, announce } = useAnnounce();

  const showDeprecated = search.deprecated === "show";
  const allRows = definitions.data?.items ?? [];
  const rows = showDeprecated ? allRows : allRows.filter((row) => !isDeprecated(row));

  const hardFailure = definitions.isError && definitions.data === undefined;
  const staleData = definitions.isError && definitions.data !== undefined;
  const hardFailureMessage = hardFailure
    ? (refusalDetail(definitions.error) ?? HARD_FAILURE_MESSAGE)
    : null;

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

  function handleShowDeprecatedChange(event: ChangeEvent<HTMLInputElement>) {
    const show = event.target.checked;
    void navigate({
      search: () => (show ? { deprecated: "show" as const } : {}),
    });
    const visible = show
      ? allRows.length
      : allRows.filter((row) => !isDeprecated(row)).length;
    announce(`Showing ${propertyCount(visible)}.`);
  }

  return (
    <section aria-labelledby="property-list-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id="property-list-heading"
          title="Property registry"
          meta={definitions.data ? summaryText(allRows) : undefined}
        />

        {definitions.isPending && <p>Loading the property registry…</p>}

        {hardFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p>{STALE_DATA_WARNING}</p>}

        {definitions.data && (
          <>
            <Checkbox
              label="Show deprecated properties"
              checked={showDeprecated}
              onChange={handleShowDeprecatedChange}
            />

            <div className="overflow-x-auto">
              <DataTable
                caption="Registry properties"
                rows={rows}
                getRowKey={(row: Row) => row.key}
                emptyState={<p className="m-0">{emptyStateText(allRows.length)}</p>}
                columns={[
                  {
                    key: "key",
                    header: "Key",
                    isRowHeader: true,
                    render: (row: Row) => (
                      <Link
                        to="/admin/properties/$propertyKey"
                        params={{ propertyKey: row.key }}
                        className="font-mono text-[var(--color-accent)] hover:underline"
                      >
                        {row.key}
                      </Link>
                    ),
                  },
                  { key: "label", header: "Label", render: (row: Row) => row.label },
                  {
                    key: "datatype",
                    header: "Datatype",
                    render: (row: Row) => (
                      <span className="font-mono">{row.datatype}</span>
                    ),
                  },
                  {
                    key: "scope",
                    header: "Scope",
                    render: (row: Row) => scopeLabelFor(row.scope),
                  },
                  {
                    key: "status",
                    header: "Status",
                    render: (row: Row) => (
                      <StatusBadge
                        tone={statusToneFor(row.status)}
                        label={statusLabelFor(row.status)}
                      />
                    ),
                  },
                ]}
              />
            </div>
          </>
        )}
      </PageContainer>
    </section>
  );
}
