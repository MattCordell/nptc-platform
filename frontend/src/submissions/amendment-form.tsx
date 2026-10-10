import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useCreateAmendment, useEntryDetail, useSession } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import { statusLabelFor } from "../catalogue/status-options.ts";
import { useCodeSelection } from "../catalogue/use-code-selection.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Button } from "../components/button.tsx";
import { Card } from "../components/card.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import type { SubmitOutcome } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { NotFoundPage } from "../shell/not-found-page.tsx";
import { SubmissionCodeField } from "./code-field.tsx";
import {
  FIELD_IDS,
  amendmentBody,
  initialValues,
  validateAmendment,
} from "./form-state.ts";
import type { NameRow, SubmissionValues } from "./form-state.ts";
import { OtherNamesField } from "./other-names-field.tsx";
import { readRefusal } from "./refusals.ts";
import { SupportFields } from "./support-fields.tsx";

/**
 * Proposing new names or a code for an existing entry (FR-35), reached from
 * the entry's "Propose a change" link as `/submissions/new?entry=<key>`.
 *
 * The entry's own details are shown and not editable: an amendment adds names
 * and may name a code, and it changes nothing else. There is no duplicate step,
 * because the amendment names the entry it is for. The server refuses a name or
 * code the entry already holds, and a refusal is shown beside the field it
 * names. No permission check runs here (NFR-20): the server refuses a caller
 * without `amendment.propose` and its sentence is shown.
 */

type EntryDetail = components["schemas"]["EntryDetail"];

const HEADING_ID = "submission-amendment-heading";
const DONE_HEADING_ID = "submission-amendment-done-heading";

const LOAD_FAILURE = "This entry could not be loaded. Try again in a moment.";
const NO_LONGER_AMENDABLE = "This entry can no longer be amended.";
const FALLBACK_MESSAGE =
  "The change could not be proposed. Check your connection and try again, or contact an administrator if the problem persists.";

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

export function AmendmentForm({ entryKey }: { entryKey: string }) {
  const entry = useEntryDetail(entryKey);
  const { message, politeness, announce } = useAnnounce();

  const hardFailure =
    entry.isError && entry.data === undefined && !isNotFound(entry.error);
  useEffect(() => {
    if (hardFailure) {
      announce(LOAD_FAILURE);
    }
  }, [hardFailure, announce]);

  if (isNotFound(entry.error)) {
    return <NotFoundPage />;
  }
  if (entry.data !== undefined) {
    return entry.data.status === "active" ? (
      <AmendmentView entry={entry.data} />
    ) : (
      <NotAmendable entry={entry.data} />
    );
  }
  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="py-6">
        <PageHeader id={HEADING_ID} title="Propose a change" />
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

/**
 * Shown in place of the form for an entry that is not active, such as a deprecated
 * one reached by a saved link. The server refuses it too; this saves the user typing
 * a proposal that cannot be sent.
 */
function NotAmendable({ entry }: { entry: EntryDetail }) {
  return (
    <section aria-labelledby={HEADING_ID}>
      <PageContainer className="py-6">
        <PageHeader id={HEADING_ID} title="Propose a change" />
        <Card>
          <div className="flex flex-col gap-4">
            <p className="m-0">
              {NO_LONGER_AMENDABLE} &ldquo;{entry.preferred_term}&rdquo; is{" "}
              {statusLabelFor(entry.status).toLowerCase()}, and only an active entry can
              be changed.
            </p>
            <div>
              <Link
                to="/catalogue/$businessKey"
                params={{ businessKey: entry.business_key }}
                className={buttonClassName("secondary")}
              >
                Back to the entry
              </Link>
            </div>
          </div>
        </Card>
      </PageContainer>
    </section>
  );
}

function CurrentEntry({ entry }: { entry: EntryDetail }) {
  const names = entry.designations
    .filter((designation) => designation.status === "active")
    .map((designation) => designation.term);
  return (
    <Card>
      <h2 className="mt-0">The entry now</h2>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2">
        <dt className="text-[var(--color-text-muted)]">Test name</dt>
        <dd className="m-0">
          <Link to="/catalogue/$businessKey" params={{ businessKey: entry.business_key }}>
            {entry.preferred_term}
          </Link>
        </dd>
        <dt className="text-[var(--color-text-muted)]">Other names</dt>
        <dd className="m-0">
          {names.length === 0 ? (
            "None"
          ) : (
            <ul className="m-0 list-none p-0">
              {names.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-[var(--color-text-muted)]">SNOMED CT code</dt>
        <dd className="m-0">
          {entry.code === null ? "None" : <CodeChip code={entry.code} />}
        </dd>
      </dl>
    </Card>
  );
}

function AmendmentView({ entry }: { entry: EntryDetail }) {
  const session = useSession();
  const create = useCreateAmendment();
  const { message, politeness, announce } = useAnnounce();
  const [values, setValues] = useState<SubmissionValues>(initialValues);
  const [errors, setErrors] = useState<FormError[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const doneHeading = useRef<HTMLHeadingElement>(null);
  const selection = useCodeSelection(values.code);

  const user = session.data?.user ?? null;

  useEffect(() => {
    if (submitted) {
      doneHeading.current?.focus();
    }
  }, [submitted]);

  function errorFor(fieldId: string): string | undefined {
    return errors.find((error) => error.fieldId === fieldId)?.message;
  }

  /** Applies an edit and drops the errors that described the text it replaced. */
  function edit(patch: Partial<SubmissionValues>, clears: (fieldId: string) => boolean) {
    setValues((previous) => ({ ...previous, ...patch }));
    setErrors((current) =>
      current.some((error) => clears(error.fieldId))
        ? current.filter((error) => !clears(error.fieldId))
        : current,
    );
    setFormError(null);
  }

  function refuse(error: unknown): SubmitOutcome {
    setErrors([]);
    if (error instanceof ApiError && error.status === 409) {
      setFormError([NO_LONGER_AMENDABLE, refusalDetail(error)].filter(Boolean).join(" "));
      return { ok: false };
    }
    const outcome = readRefusal(error, {}, FALLBACK_MESSAGE);
    if (outcome.kind === "refused") {
      setErrors(outcome.fieldErrors);
      setFormError(outcome.message);
    } else {
      setFormError(FALLBACK_MESSAGE);
    }
    return { ok: false };
  }

  async function submit(): Promise<SubmitOutcome> {
    setFormError(null);
    const found = validateAmendment(values, selection);
    setErrors(found);
    if (found.length > 0) {
      return { ok: false };
    }
    try {
      await create.mutateAsync(amendmentBody(entry.business_key, values, selection));
      setSubmitted(true);
      announce("Change proposed.");
      return { ok: true };
    } catch (error) {
      return refuse(error);
    }
  }

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader id={HEADING_ID} title="Propose a change" />

        {submitted ? (
          <Card>
            <section aria-labelledby={DONE_HEADING_ID} className="flex flex-col gap-4">
              <h2 id={DONE_HEADING_ID} ref={doneHeading} tabIndex={-1}>
                Your change was proposed
              </h2>
              <p className="m-0">
                A reviewer will check your proposal for &ldquo;{entry.preferred_term}
                &rdquo; before the entry changes.
              </p>
              <div className="flex items-center gap-2">
                <Link
                  to="/catalogue/$businessKey"
                  params={{ businessKey: entry.business_key }}
                  className={buttonClassName("primary")}
                >
                  Back to the entry
                </Link>
                <Link to="/catalogue" className={buttonClassName("secondary")}>
                  Back to the catalogue
                </Link>
              </div>
            </section>
          </Card>
        ) : (
          <>
            <p className="m-0">
              Suggest other names or a SNOMED CT code for this entry. A reviewer checks
              your proposal before the entry changes.
            </p>

            <CurrentEntry entry={entry} />

            <Card>
              <Form
                submitLabel="Propose change"
                pendingLabel="Proposing"
                pending={create.isPending}
                errors={errors}
                formError={formError ?? undefined}
                onSubmit={submit}
                secondaryActions={
                  <Link
                    to="/catalogue/$businessKey"
                    params={{ businessKey: entry.business_key }}
                    className={buttonClassName("secondary")}
                  >
                    Cancel
                  </Link>
                }
              >
                <OtherNamesField
                  legend="New other names"
                  description="Other names to add to this entry. Paste a list separated by semicolons to add several at once."
                  rows={values.names}
                  error={errorFor(FIELD_IDS.synonyms)}
                  onChange={(names: NameRow[]) =>
                    edit({ names }, (id) => id === FIELD_IDS.synonyms)
                  }
                />

                <SubmissionCodeField
                  selection={selection}
                  error={errorFor(FIELD_IDS.snomed_code)}
                  onPick={(code) => edit({ code }, (id) => id === FIELD_IDS.snomed_code)}
                  onClear={() =>
                    edit({ code: null }, (id) => id === FIELD_IDS.snomed_code)
                  }
                />

                <SupportFields
                  values={values}
                  errorFor={errorFor}
                  onEdit={(patch, fieldId) => edit(patch, (id) => id === fieldId)}
                  defaultOrganisation={user?.organisation ?? ""}
                  referenceRequired={false}
                  noun="change"
                />

                <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6">
                  <dt className="text-[var(--color-text-muted)]">Proposed by</dt>
                  <dd className="m-0">{user?.display_name ?? "you"}</dd>
                </dl>
              </Form>
            </Card>
          </>
        )}
      </PageContainer>
    </section>
  );
}
