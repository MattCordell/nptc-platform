import { Link } from "@tanstack/react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import { Button } from "../components/button.tsx";
import { Card } from "../components/card.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { BindingRetirement } from "./binding-retirement.tsx";
import { bindingStatus } from "./binding-status.ts";
import { statusLabelFor, statusToneFor } from "./status-options.ts";

type EntryDetail = components["schemas"]["EntryDetail"];
type Binding = components["schemas"]["Binding"];

const NO_MATCH_FALLBACK = "No published catalogue entry matches this system and code.";
const REFUSED =
  "The catalogue does not accept this system or code. Check both and look up again.";
const LOAD_FAILURE = "This code could not be looked up. Try again in a moment.";

type Outcome = "idle" | "loading" | "match" | "no-match" | "refused" | "failed";

function outcomeOf(query: UseQueryResult<EntryDetail, Error>): Outcome {
  const { error, data } = query;
  if (error instanceof ApiError && error.status === 404) {
    return "no-match";
  }
  if (error instanceof ApiError && error.status === 422) {
    return "refused";
  }
  if (data !== undefined) {
    return "match";
  }
  if (query.isError) {
    return "failed";
  }
  return query.isFetching ? "loading" : "idle";
}

/**
 * The binding the lookup hit. An entry can bind one code twice (retired, then
 * bound again), so an active row wins over a retired one.
 */
function matchedBinding(entry: EntryDetail, code: string): Binding | undefined {
  return (
    entry.bindings.find((b) => b.code === code && b.status === "active") ??
    entry.bindings.find((b) => b.code === code)
  );
}

function Match({ entry, code }: { entry: EntryDetail; code: string }) {
  const binding = matchedBinding(entry, code);
  const status = binding === undefined ? undefined : bindingStatus(binding.status);
  return (
    <Card
      role="region"
      aria-labelledby="code-lookup-result-heading"
      className="flex flex-col gap-3"
    >
      <h2 id="code-lookup-result-heading" className="m-0 text-xl">
        Matching entry
      </h2>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2">
        <dt className="text-[var(--color-text-muted)]">Entry</dt>
        <dd className="m-0">
          <Link
            to="/catalogue/$businessKey"
            params={{ businessKey: entry.business_key }}
            className="inline-flex min-h-6 items-center text-[var(--color-accent)] underline underline-offset-2 hover:text-[var(--color-accent-hover)]"
          >
            {entry.preferred_term}
          </Link>{" "}
          <span className="text-[var(--color-text-muted)]">({entry.business_key})</span>
        </dd>
        <dt className="text-[var(--color-text-muted)]">Entry status</dt>
        <dd className="m-0">
          <StatusBadge
            tone={statusToneFor(entry.status)}
            label={statusLabelFor(entry.status)}
          />
        </dd>
        <dt className="text-[var(--color-text-muted)]">Code</dt>
        <dd className="m-0 flex flex-wrap items-center gap-2">
          <CodeChip code={code} />
          {status === undefined ? null : (
            <StatusBadge tone={status.tone} label={status.label} />
          )}
        </dd>
        {binding !== undefined && binding.status !== "active" ? (
          <>
            <dt className="text-[var(--color-text-muted)]">Retirement</dt>
            <dd className="m-0">
              <BindingRetirement binding={binding} />
            </dd>
          </>
        ) : null}
      </dl>
      {binding !== undefined && binding.status !== "active" ? (
        <p className="m-0">This code is retired on this entry.</p>
      ) : null}
    </Card>
  );
}

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card
      role="region"
      aria-labelledby="code-lookup-result-heading"
      className="flex flex-col gap-3"
    >
      <h2 id="code-lookup-result-heading" className="m-0 text-xl">
        {title}
      </h2>
      {children}
    </Card>
  );
}

/**
 * What a code lookup answered (FR-17): the entry that binds the code, or a
 * plain sentence saying none does.
 *
 * Takes the query rather than running it so the form page, which looks up by
 * system URI, and the direct page, which looks up by token, show one result.
 * The server answers an unregistered system and an unknown code with the same
 * 404, so the not-found text never says which of the two was wrong. A retired
 * code still resolves, and the match says so.
 */
export function CodeLookupResult({
  query,
  code,
}: {
  query: UseQueryResult<EntryDetail, Error>;
  code: string;
}) {
  const { message, politeness, announce } = useAnnounce();
  const outcome = outcomeOf(query);
  const found = outcome === "match" ? query.data : undefined;
  const preferredTerm = found?.preferred_term;

  useEffect(() => {
    if (outcome === "match" && preferredTerm !== undefined) {
      announce(`Found the entry ${preferredTerm}.`);
    } else if (outcome === "no-match") {
      announce("No entry matches this code.");
    } else if (outcome === "refused") {
      announce(REFUSED);
    } else if (outcome === "failed") {
      announce(LOAD_FAILURE, "assertive");
    }
  }, [outcome, preferredTerm, announce]);

  return (
    <>
      <LiveRegion message={message} politeness={politeness} />
      {outcome === "loading" ? <p className="m-0">Looking up the code…</p> : null}
      {outcome === "match" && found !== undefined ? (
        <Match entry={found} code={code} />
      ) : null}
      {outcome === "no-match" ? (
        <Notice title="No matching entry">
          <p className="m-0">{refusalDetail(query.error) ?? NO_MATCH_FALLBACK}</p>
        </Notice>
      ) : null}
      {outcome === "refused" ? (
        <Notice title="Code not accepted">
          <p className="m-0">{REFUSED}</p>
        </Notice>
      ) : null}
      {outcome === "failed" ? (
        <Notice title="Lookup failed">
          <p className="m-0 text-[var(--color-danger)]">{LOAD_FAILURE}</p>
          <div>
            <Button
              type="button"
              variant="secondary"
              onClick={() => void query.refetch()}
            >
              Try again
            </Button>
          </div>
        </Notice>
      ) : null}
    </>
  );
}
