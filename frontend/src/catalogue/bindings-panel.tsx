import { useEffect, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import {
  useBindCode,
  useConceptLookup,
  useReplaceBinding,
  useRetireBinding,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { DataTable } from "../components/data-table.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { RefusalNotice } from "./collision-notice.tsx";

/**
 * The code-binding editing panel (issue #150; FR-06, FR-08, FR-26, FR-36, FR-82).
 *
 * **The editor only ever types a code.** FR-26 requires the FSN and active
 * status to be resolved live against the terminology server, and that
 * guarantee is stronger than the requirement asked for: `useConceptLookup`
 * (issue #240's route) is the only source of `fsn`/`au_preferred_term` this
 * panel ever sends, so no editor keystroke can reach either label. This is
 * also why there is no Verhoeff check anywhere in this file - a code that
 * fails the check digit cannot exist in SNOMED CT, so the lookup already
 * answers the question, and `nptc_shared.sctid`'s Postgres-level
 * counterpart stays the authority server-side (ADR-0030's amendment).
 *
 * **`fsn` is rendered verbatim; `display_term` is not rendered at all.**
 * `Binding` carries both, but this is an editing screen, not an export
 * surface - showing the stripped form here would invite exactly the
 * double-stripping confusion FR-83 exists to prevent. Never reference
 * `display_term`, `strip_semantic_tag`, `semantic_tag` or
 * `render_display_term` in this module.
 *
 * **Retired bindings are listed, unlike the designations panel.**
 * `GET .../bindings`'s own docstring requires it under FR-08: a client
 * holding a retired code learns so here, with the reason and any successor.
 *
 * **Retire and Replace are two separate actions.** `/replacement` is the
 * only route that populates `replaced_by_code` - plain retirement can never
 * record a supersession, and one dialog with a conditional branch would hide
 * that distinction.
 */

type EntryDetail = components["schemas"]["EntryDetail"];
type Binding = components["schemas"]["Binding"];
type CodeBindingEditionHint = components["schemas"]["CodeBindingEditionHint"];

const NOTE_HINT =
  "This becomes the published History text, so describe the change - single words " +
  "like “update” or “fix” are not accepted.";

/** Client-side check only for emptiness; FR-37's substance is the server's. */
function reasonError(reason: string, fieldId: string): FormError[] {
  return reason.trim().length === 0
    ? [{ fieldId, message: "Enter a changelog note describing this change." }]
    : [];
}

/**
 * `ConceptLookup.edition` is a bare `string` (always `"au"` today, per its
 * own docstring), but `BindCodeRequest.edition_hint` is the closed
 * `CodeBindingEditionHint` enum. Narrowed here, once, rather than trusted -
 * an edition the server starts reporting that this screen does not
 * recognise should fall back to `"unknown"`, not crash the bind.
 */
function toEditionHint(edition: string): CodeBindingEditionHint {
  return edition === "au" || edition === "int" ? edition : "unknown";
}

/**
 * Debounces a fast-changing value, so a code lookup fires once typing pauses
 * rather than once per keystroke (FR-52's spirit, applied to an interactive
 * caller rather than the batch sweep it was written for).
 */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeout);
  }, [value, delayMs]);
  return debounced;
}

/**
 * The read-only result of resolving `code`, rendered as a `Field` hint so it
 * is linked to the input via `aria-describedby` without being a second
 * control. Deliberately not a live region: it changes on every debounced
 * lookup, and a screen-reader user would be interrupted while still typing
 * (the same reasoning `AddSynonymsForm`'s preview uses in the designations
 * panel).
 *
 * Returns phrasing content only (text and `<span>`, never `<p>`/`<dl>`):
 * `Field` already wraps its `hint` in a `<p>`, and block content nested
 * inside a `<p>` is invalid HTML - a real bug an earlier version of this
 * component had.
 */
function ResolvedConcept({
  code,
  lookup,
}: {
  code: string;
  lookup: ReturnType<typeof useConceptLookup>;
}) {
  if (code.length === 0) {
    return null;
  }
  if (lookup.isPending) {
    return <>Checking {code} against the terminology server…</>;
  }
  if (lookup.isError) {
    // The server's own sentence distinguishes "does not exist" (404) from
    // "could not be reached" (503/502) - FR-26 and FR-54's whole point is
    // that an editor needs to know which, so this renders whatever
    // `nptc.api.errors` wrote rather than one generic line.
    return (
      <>{refusalDetail(lookup.error) ?? "This code could not be checked. Try again."}</>
    );
  }
  const concept = lookup.data;
  if (concept.fsn === null) {
    return (
      <>
        The terminology server did not return a name for {code}. It cannot be bound until
        it does.
      </>
    );
  }
  const status =
    concept.active === null
      ? "not reported by the terminology server"
      : concept.active
        ? "active"
        : "inactive";
  return (
    <>
      Fully specified name: <span>{concept.fsn}</span>. AU preferred term:{" "}
      <span>{concept.au_preferred_term ?? "not reported"}</span>. Status:{" "}
      <span>{status}</span>.
    </>
  );
}

/** True once `code` has resolved to a bindable concept: fetched, settled, and named. */
function isBindable(
  code: string,
  debouncedCode: string,
  lookup: ReturnType<typeof useConceptLookup>,
): lookup is ReturnType<typeof useConceptLookup> & {
  data: NonNullable<ReturnType<typeof useConceptLookup>["data"]> & { fsn: string };
} {
  return code === debouncedCode && lookup.isSuccess && lookup.data.fsn !== null;
}

function BindCodeForm({
  businessKey,
  onSaved,
}: {
  businessKey: string;
  onSaved: () => void;
}) {
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const trimmedCode = code.trim();
  const debouncedCode = useDebouncedValue(trimmedCode, 400);
  const lookup = useConceptLookup(debouncedCode);
  const bind = useBindCode(businessKey);
  const bindable = isBindable(trimmedCode, debouncedCode, lookup);

  return (
    <Form
      submitLabel="Bind code"
      pendingLabel="Binding"
      pending={bind.isPending}
      errors={errors}
      formError={bind.isError ? <RefusalNotice error={bind.error} /> : undefined}
      errorSummaryHeadingLevel={3}
      onSubmit={() => {
        const found: FormError[] = [
          ...(trimmedCode.length === 0
            ? [{ fieldId: "bind-code", message: "Enter the SNOMED CT code to bind." }]
            : !bindable
              ? [
                  {
                    fieldId: "bind-code",
                    message:
                      "This code must resolve against the terminology server before it can be bound.",
                  },
                ]
              : []),
          ...reasonError(note, "bind-note"),
        ];
        setErrors(found);
        if (found.length > 0 || !bindable) {
          return;
        }
        bind.mutate(
          {
            code: debouncedCode,
            fsn: lookup.data.fsn,
            au_preferred_term: lookup.data.au_preferred_term,
            edition_hint: toEditionHint(lookup.data.edition),
            reason: note,
          },
          {
            onSuccess: () => {
              setCode("");
              setNote("");
              onSaved();
            },
          },
        );
      }}
    >
      <h3>Bind a code</h3>
      <Field
        id="bind-code"
        label="SNOMED CT code"
        hint={<ResolvedConcept code={debouncedCode} lookup={lookup} />}
        error={errors.find((error) => error.fieldId === "bind-code")?.message}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            inputMode="numeric"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        )}
      </Field>
      <Field
        id="bind-note"
        label="Changelog note"
        hint={NOTE_HINT}
        error={errors.find((error) => error.fieldId === "bind-note")?.message}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            type="text"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        )}
      </Field>
    </Form>
  );
}

function RetireBindingDialog({
  businessKey,
  binding,
  onClose,
  onSaved,
}: {
  businessKey: string;
  binding: Binding;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const retire = useRetireBinding(businessKey);

  return (
    <Dialog open onClose={onClose} title={`Retire ${binding.code}`}>
      <Form
        submitLabel="Retire binding"
        pendingLabel="Retiring"
        pending={retire.isPending}
        errors={errors}
        formError={retire.isError ? <RefusalNotice error={retire.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = reasonError(note, "retire-binding-note");
          setErrors(found);
          if (found.length > 0) {
            return;
          }
          retire.mutate(
            { code: binding.code, body: { reason: note } },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <p>
          This stops {binding.code} being this entry&rsquo;s active binding. It is not
          deleted: the row stays in the table above, retired, with this reason (FR-08).
        </p>
        <Field
          id="retire-binding-note"
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === "retire-binding-note")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
      </Form>
    </Dialog>
  );
}

function ReplaceBindingDialog({
  businessKey,
  binding,
  onClose,
  onSaved,
}: {
  businessKey: string;
  binding: Binding;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const trimmedCode = code.trim();
  const debouncedCode = useDebouncedValue(trimmedCode, 400);
  const lookup = useConceptLookup(debouncedCode);
  const replace = useReplaceBinding(businessKey);
  const bindable = isBindable(trimmedCode, debouncedCode, lookup);

  return (
    <Dialog open onClose={onClose} title={`Replace ${binding.code}`}>
      <Form
        submitLabel="Replace"
        pendingLabel="Replacing"
        pending={replace.isPending}
        errors={errors}
        formError={replace.isError ? <RefusalNotice error={replace.error} /> : undefined}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found: FormError[] = [
            ...(trimmedCode.length === 0
              ? [
                  {
                    fieldId: "replace-code",
                    message: "Enter the successor's SNOMED CT code.",
                  },
                ]
              : !bindable
                ? [
                    {
                      fieldId: "replace-code",
                      message:
                        "The successor must resolve against the terminology server before it can be bound.",
                    },
                  ]
                : []),
            ...reasonError(note, "replace-note"),
          ];
          setErrors(found);
          if (found.length > 0 || !bindable) {
            return;
          }
          replace.mutate(
            {
              code: binding.code,
              body: {
                successor: {
                  code: debouncedCode,
                  fsn: lookup.data.fsn,
                  au_preferred_term: lookup.data.au_preferred_term,
                  edition_hint: toEditionHint(lookup.data.edition),
                },
                reason: note,
              },
            },
            { onSuccess: () => onSaved() },
          );
        }}
      >
        <p>
          Retires {binding.code} and binds its successor in one change. One reason covers
          both steps (FR-08).
        </p>
        <Field
          id="replace-code"
          label="Successor SNOMED CT code"
          hint={<ResolvedConcept code={debouncedCode} lookup={lookup} />}
          error={errors.find((error) => error.fieldId === "replace-code")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              inputMode="numeric"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          )}
        </Field>
        <Field
          id="replace-note"
          label="Changelog note"
          hint={NOTE_HINT}
          error={errors.find((error) => error.fieldId === "replace-note")?.message}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
      </Form>
    </Dialog>
  );
}

/** Active first, then retired - the order the plan for this screen calls for. */
function sortedBindings(bindings: Binding[]): Binding[] {
  const active = bindings.filter((binding) => binding.status === "active");
  const retired = bindings.filter((binding) => binding.status !== "active");
  return [...active, ...retired];
}

export function BindingsPanel({ entry }: { entry: EntryDetail }) {
  const businessKey = entry.business_key;
  const [retiring, setRetiring] = useState<Binding | null>(null);
  const [replacing, setReplacing] = useState<Binding | null>(null);
  const { message, politeness, announce } = useAnnounce();

  const rows = sortedBindings(entry.bindings);
  const hasActiveBinding = rows.some((binding) => binding.status === "active");

  return (
    <section aria-labelledby="bindings-heading">
      <h2 id="bindings-heading">Code bindings</h2>
      <p>
        The SNOMED CT codes bound to this entry. Retired bindings stay listed, with the
        reason and any successor code (FR-08).
      </p>

      <LiveRegion message={message} politeness={politeness} />

      <DataTable
        caption={`Code bindings on ${businessKey}`}
        columns={[
          { key: "code", header: "Code", isRowHeader: true, render: (row) => row.code },
          { key: "fsn", header: "Fully specified name", render: (row) => row.fsn },
          {
            key: "au_preferred_term",
            header: "AU preferred term",
            render: (row) => row.au_preferred_term ?? "—",
          },
          { key: "status", header: "Status", render: (row) => row.status },
          {
            key: "retirement",
            header: "Retirement",
            render: (row) =>
              row.status === "retired" ? (
                <>
                  {row.retirement_reason}
                  {row.replaced_by_code !== null && (
                    <> — replaced by {row.replaced_by_code}</>
                  )}
                </>
              ) : (
                "—"
              ),
          },
          {
            key: "actions",
            header: "Actions",
            render: (row) =>
              row.status === "active" ? (
                <span className="flex gap-2">
                  <Button
                    type="button"
                    variant="danger"
                    aria-label={`Retire ${row.code}`}
                    onClick={() => setRetiring(row)}
                  >
                    Retire
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    aria-label={`Replace ${row.code}`}
                    onClick={() => setReplacing(row)}
                  >
                    Replace
                  </Button>
                </span>
              ) : null,
          },
        ]}
        rows={rows}
        getRowKey={(row) => `${row.code}:${row.status}`}
        emptyState="This entry has no code bindings."
      />

      {/* Shown only while the entry has no active binding: FR-08 permits at
          most one, and offering a form that could only ever 409 would be the
          mistake the designations panel avoids by not offering Retire on the
          preferred term. */}
      {!hasActiveBinding && (
        <BindCodeForm businessKey={businessKey} onSaved={() => announce("Code bound.")} />
      )}

      {retiring !== null && (
        <RetireBindingDialog
          businessKey={businessKey}
          binding={retiring}
          onClose={() => setRetiring(null)}
          onSaved={() => {
            setRetiring(null);
            announce("Binding retired.");
          }}
        />
      )}

      {replacing !== null && (
        <ReplaceBindingDialog
          businessKey={businessKey}
          binding={replacing}
          onClose={() => setReplacing(null)}
          onSaved={() => {
            setReplacing(null);
            announce("Binding replaced.");
          }}
        />
      )}
    </section>
  );
}
