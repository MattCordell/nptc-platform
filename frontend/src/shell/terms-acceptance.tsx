import { Link } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import type { components } from "../api/schema.ts";
import { asTermsVersionStale } from "../api/terms.ts";
import { formatDate } from "../catalogue/format-date.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { Checkbox } from "../components/checkbox.tsx";
import { Form, type SubmitOutcome } from "../components/form.tsx";
import { Markdown } from "../components/markdown.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useFocusHeadingOnMount } from "../components/use-focus-heading-on-mount.ts";
import { COLLECTION_NOTICE } from "./collection-notice.ts";

type CurrentTerms = components["schemas"]["CurrentTermsResponse"];

const HEADING_ID = "terms-acceptance-heading";

function CollectionNotice() {
  return (
    <Card className="flex max-w-3xl flex-col gap-3">
      <h2 className="m-0 text-xl">{COLLECTION_NOTICE.title}</h2>
      <p className="m-0">{COLLECTION_NOTICE.collect}</p>
      <p className="m-0">{COLLECTION_NOTICE.visible}</p>
      <p className="m-0">{COLLECTION_NOTICE.retention}</p>
      <p className="m-0">{COLLECTION_NOTICE.access}</p>
      <p className="m-0">
        <Link
          to="/privacy"
          className="inline-flex min-h-6 items-center text-[var(--color-accent)] underline underline-offset-2 hover:text-[var(--color-accent-hover)]"
        >
          {COLLECTION_NOTICE.privacyLink}
        </Link>
      </p>
    </Card>
  );
}

/**
 * The full-page acceptance gate (NFR-45, ADR-0043): the current terms, the
 * collection notice, and an explicit tick-box before the user may continue.
 * It only presents. The server refuses every contribution until acceptance is
 * recorded, so nothing here is access control.
 *
 * Mounted under a `key` of the terms version, so a version that changes while
 * the user reads starts a fresh form: the box is unticked, because the user
 * has not read the new text. The submit error lives in the parent's mutation,
 * so a stale-version refusal survives that remount and is shown as the
 * "terms changed" notice instead of a form error.
 */
export function TermsAcceptance({
  terms,
  pending,
  error,
  onAccept,
}: {
  terms: CurrentTerms;
  pending: boolean;
  error: unknown;
  onAccept: (version: string) => Promise<SubmitOutcome>;
}) {
  const [checked, setChecked] = useState(false);
  const checkboxId = useId();
  const changeNoticeRef = useRef<HTMLParagraphElement>(null);

  const stale = asTermsVersionStale(error);
  const termsChanged = stale !== null && stale.current_version === terms.version;
  // Read once, on mount: focus goes to the change notice when this form
  // replaced one under an older version, and to the heading otherwise.
  const [openedOnChange] = useState(termsChanged);
  useFocusHeadingOnMount(HEADING_ID, !openedOnChange);
  useEffect(() => {
    if (openedOnChange) {
      changeNoticeRef.current?.focus();
    }
  }, [openedOnChange]);

  const formError =
    error === null || error === undefined || termsChanged
      ? undefined
      : (refusalDetail(error) ?? "Your acceptance could not be saved. Try again.");

  return (
    <PageContainer className="py-6">
      <PageHeader
        id={HEADING_ID}
        focusable
        title="Accept the terms of use"
        meta={
          <>
            Version {terms.version}, effective{" "}
            <time dateTime={terms.effective_date}>
              {formatDate(`${terms.effective_date}T00:00:00`)}
            </time>
          </>
        }
      />
      {termsChanged ? (
        <p
          ref={changeNoticeRef}
          tabIndex={-1}
          className="m-0 max-w-3xl rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-semibold"
        >
          The terms of use changed while you were reading them. Read the new version
          below, then accept it.
        </p>
      ) : null}
      <p className="m-0 max-w-3xl">
        You need to accept these terms before you can make changes to the catalogue. You
        can still read the public catalogue without accepting.
      </p>
      <CollectionNotice />
      <Card className="max-w-3xl">
        <Markdown text={terms.text} />
      </Card>
      <Card className="max-w-3xl">
        <Form
          onSubmit={() => onAccept(terms.version)}
          pending={pending}
          submitBlocked={!checked}
          blockedReason="Tick the box to say you accept the terms of use."
          blockedFieldId={checkboxId}
          formError={formError}
          submitLabel="Accept and continue"
          pendingLabel="Saving your acceptance…"
          secondaryActions={
            <Link to="/sign-out" className={buttonClassName("secondary")}>
              Sign out
            </Link>
          }
        >
          <Checkbox
            id={checkboxId}
            label={`I have read and accept the terms of use (version ${terms.version})`}
            checked={checked}
            onChange={(event) => setChecked(event.target.checked)}
          />
          <p className="m-0 text-sm text-[var(--color-text-muted)]">
            If you do not accept, sign out. Your account stays, and you can accept later.
          </p>
        </Form>
      </Card>
    </PageContainer>
  );
}
