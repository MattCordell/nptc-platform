import { Link, useSearch } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import type { DuplicateMatch } from "../api/conflicts.ts";
import { refusalDetail } from "../api/conflicts.ts";
import {
  useCreateSubmission,
  useDuplicateCheck,
  useSession,
  useSubmissionPropertyDefinitions,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { CONTROLS, RepeatableValues } from "../catalogue/property-controls/index.ts";
import type { PropertyValueSlot } from "../catalogue/property-controls/index.ts";
import { useCodeSelection } from "../catalogue/use-code-selection.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Button } from "../components/button.tsx";
import { Card } from "../components/card.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import type { SubmitOutcome } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { AmendmentForm } from "../submissions/amendment-form.tsx";
import { SubmissionCodeField } from "../submissions/code-field.tsx";
import { DuplicatePanel } from "../submissions/duplicate-panel.tsx";
import {
  FIELD_IDS,
  createBody,
  duplicateCheckBody,
  initialValues,
  submissionRows,
  submittedIndexesByKey,
  validate,
} from "../submissions/form-state.ts";
import type { NameRow, SubmissionValues } from "../submissions/form-state.ts";
import { OtherNamesField } from "../submissions/other-names-field.tsx";
import { readRefusal } from "../submissions/refusals.ts";
import { SupportFields } from "../submissions/support-fields.tsx";

/**
 * Proposing a new test (FR-23 to FR-27, FR-43, FR-54).
 *
 * The properties offered, and which are required, come from the registry's
 * submission scope, so an administrator adds one by changing its scope and no
 * property is named here (FR-09, FR-24). A refusal is shown beside the field it
 * names, by the `field` the server sends and never by its wording.
 *
 * The duplicate step is a panel on this page, not another route, and the form
 * stays mounted behind it so nothing the user typed is lost. The same holds
 * across the terms gate (NFR-45): it hides the page and does not unmount it.
 * No permission check runs here (NFR-20): the server refuses a caller without
 * `submission.create` and its sentence is shown.
 */

type Submission = components["schemas"]["SubmissionResponse"];
type Stage = "form" | "duplicates" | "submitted";

const HEADING_ID = "submission-new-heading";
const DONE_HEADING_ID = "submission-done-heading";

function isPropertyField(fieldId: string, key: string): boolean {
  return fieldId.startsWith(`${key}-`);
}

/**
 * `?entry=<business key>` opens the form to amend that entry instead (FR-35).
 * The amendment form is its own component, so a new test's form holds nothing
 * for it.
 */
export function SubmissionNewPage() {
  const { entry } = useSearch({ from: "/authenticated/submissions/new" });
  return entry === undefined ? (
    <NewTestForm />
  ) : (
    <AmendmentForm key={entry} entryKey={entry} />
  );
}

function NewTestForm() {
  const session = useSession();
  const definitions = useSubmissionPropertyDefinitions();
  const duplicateCheck = useDuplicateCheck();
  const create = useCreateSubmission();
  const { message, politeness, announce } = useAnnounce();
  const [formKey, setFormKey] = useState(0);
  const [values, setValues] = useState<SubmissionValues>(initialValues);
  const [errors, setErrors] = useState<FormError[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>("form");
  const [matches, setMatches] = useState<DuplicateMatch[]>([]);
  const [panelKey, setPanelKey] = useState(0);
  const [submitted, setSubmitted] = useState<Submission | null>(null);
  const duplicatesHeading = useRef<HTMLHeadingElement>(null);
  const doneHeading = useRef<HTMLHeadingElement>(null);
  const selection = useCodeSelection(values.code);

  const rows = submissionRows(definitions.data?.items ?? []);
  const user = session.data?.user ?? null;
  const pending = duplicateCheck.isPending || create.isPending;

  useEffect(() => {
    if (stage === "duplicates") {
      duplicatesHeading.current?.focus();
    } else if (stage === "submitted") {
      doneHeading.current?.focus();
    }
  }, [stage]);

  function errorFor(fieldId: string): string | undefined {
    return errors.find((error) => error.fieldId === fieldId)?.message;
  }

  /**
   * Applies an edit and drops the errors that described the text it replaced. A change
   * that builds on the current values takes a function of them, so two edits made before a
   * re-render both land.
   */
  function edit(
    patch:
      | Partial<SubmissionValues>
      | ((previous: SubmissionValues) => Partial<SubmissionValues>),
    clears: (fieldId: string) => boolean,
  ) {
    setValues((previous) => ({
      ...previous,
      ...(typeof patch === "function" ? patch(previous) : patch),
    }));
    setErrors((current) =>
      current.some((error) => clears(error.fieldId))
        ? current.filter((error) => !clears(error.fieldId))
        : current,
    );
    setFormError(null);
  }

  function showMatches(found: DuplicateMatch[]) {
    setMatches(found);
    setPanelKey((key) => key + 1);
    setErrors([]);
    setFormError(null);
    setStage("duplicates");
  }

  function refuse(error: unknown): SubmitOutcome {
    const outcome = readRefusal(error, submittedIndexesByKey(values.slots));
    if (outcome.kind === "duplicates") {
      showMatches(outcome.body.matches);
      return { ok: true };
    }
    setErrors(outcome.fieldErrors);
    setFormError(outcome.message);
    if (stage === "duplicates" && outcome.fieldErrors.length > 0) {
      setStage("form");
    }
    return { ok: false };
  }

  async function send(confirmNotDuplicate: boolean): Promise<SubmitOutcome> {
    setFormError(null);
    try {
      const result = await create.mutateAsync(
        createBody(values, rows, selection, confirmNotDuplicate),
      );
      setSubmitted(result);
      setErrors([]);
      setStage("submitted");
      announce("Test submitted.");
      return { ok: true };
    } catch (error) {
      return refuse(error);
    }
  }

  async function submit(): Promise<SubmitOutcome> {
    setFormError(null);
    const found = validate(values, rows, selection);
    setErrors(found);
    if (found.length > 0) {
      return { ok: false };
    }
    try {
      const check = await duplicateCheck.mutateAsync(
        duplicateCheckBody(values, selection),
      );
      if (check.matches.length > 0) {
        showMatches(check.matches);
        return { ok: true };
      }
    } catch (error) {
      return refuse(error);
    }
    return send(false);
  }

  function startAnother() {
    setFormKey((key) => key + 1);
    setValues(initialValues());
    setErrors([]);
    setFormError(null);
    setMatches([]);
    setSubmitted(null);
    setStage("form");
    duplicateCheck.reset();
    create.reset();
  }

  function backToForm() {
    setErrors([]);
    setFormError(null);
    setStage("form");
    window.setTimeout(
      () => document.getElementById(FIELD_IDS.preferred_term)?.focus(),
      0,
    );
  }

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader id={HEADING_ID} title="Submit a new test" />

        {stage === "form" || stage === "duplicates" ? (
          <p className="m-0">
            Propose a test for the catalogue. A reviewer checks it before it is added.
            Fields marked (required) must be filled in.
          </p>
        ) : null}

        <div hidden={stage !== "form"}>
          <Card>
            <Form
              key={formKey}
              submitLabel="Submit test"
              pendingLabel="Submitting"
              pending={pending}
              errors={errors}
              formError={formError ?? undefined}
              onSubmit={submit}
              secondaryActions={
                <Link to="/catalogue" className={buttonClassName("secondary")}>
                  Cancel
                </Link>
              }
            >
              <Field
                id={FIELD_IDS.preferred_term}
                label="Test name (required)"
                hint="The name the test is requested by."
                error={errorFor(FIELD_IDS.preferred_term)}
              >
                {(controlProps) => (
                  <input
                    {...controlProps}
                    className={INPUT_CLASSES}
                    type="text"
                    value={values.preferredTerm}
                    onChange={(event) =>
                      edit(
                        { preferredTerm: event.target.value },
                        (id) => id === FIELD_IDS.preferred_term,
                      )
                    }
                  />
                )}
              </Field>

              <OtherNamesField
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
                onClear={() => edit({ code: null }, (id) => id === FIELD_IDS.snomed_code)}
              />

              {definitions.isPending && <p className="m-0">Loading the properties…</p>}
              {definitions.isError && (
                <p className="m-0">
                  {refusalDetail(definitions.error) ??
                    "The properties could not be loaded, so they are not shown. Reload the page to try again."}
                </p>
              )}
              {rows.length > 0 && <h2>Properties</h2>}
              {rows.map((definition) => {
                const Control = CONTROLS[definition.form_control.control];
                return (
                  <RepeatableValues
                    key={definition.key}
                    propertyKey={definition.key}
                    label={
                      definition.required_for_submission
                        ? `${definition.label} (required)`
                        : definition.label
                    }
                    cardinality={definition.cardinality as PropertyCardinality}
                    control={Control}
                    params={definition.form_control.params}
                    slots={values.slots[definition.key] ?? []}
                    onChange={(next: PropertyValueSlot[]) =>
                      edit(
                        (previous) => ({
                          slots: { ...previous.slots, [definition.key]: next },
                        }),
                        (id) => isPropertyField(id, definition.key),
                      )
                    }
                    errors={errors}
                  />
                );
              })}

              <SupportFields
                values={values}
                errorFor={errorFor}
                onEdit={(patch, fieldId) => edit(patch, (id) => id === fieldId)}
                defaultOrganisation={user?.organisation ?? ""}
                referenceRequired
                noun="test"
              />

              <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6">
                <dt className="text-[var(--color-text-muted)]">Submitted by</dt>
                <dd className="m-0">{user?.display_name ?? "you"}</dd>
              </dl>
            </Form>
          </Card>
        </div>

        {stage === "duplicates" && (
          <Card>
            <DuplicatePanel
              key={panelKey}
              matches={matches}
              pending={pending}
              error={formError ?? undefined}
              headingRef={duplicatesHeading}
              onConfirm={() => send(true)}
              onBack={backToForm}
            />
          </Card>
        )}

        {stage === "submitted" && submitted !== null && (
          <Card>
            <section aria-labelledby={DONE_HEADING_ID} className="flex flex-col gap-4">
              <h2 id={DONE_HEADING_ID} ref={doneHeading} tabIndex={-1}>
                Your test was submitted
              </h2>
              <p className="m-0">
                &ldquo;{submitted.preferred_term}&rdquo; is waiting for review.
              </p>
              <div className="flex items-center gap-2">
                <Button type="button" onClick={startAnother}>
                  Submit another test
                </Button>
                <Link to="/catalogue" className={buttonClassName("secondary")}>
                  Back to the catalogue
                </Link>
              </div>
            </section>
          </Card>
        )}
      </PageContainer>
    </section>
  );
}

type PropertyCardinality = components["schemas"]["PropertyCardinality"];
