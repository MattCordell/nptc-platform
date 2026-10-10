import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { RefObject } from "react";

import type { DuplicateMatch } from "../api/conflicts.ts";
import { Button } from "../components/button.tsx";
import { Checkbox } from "../components/checkbox.tsx";
import { Form } from "../components/form.tsx";
import type { SubmitOutcome } from "../components/form.tsx";

/**
 * The duplicate step (FR-25): the matches the server found, and a recorded
 * confirmation that the new test is a different one.
 *
 * It lists what matched and never who submitted it (FR-42). A catalogue entry
 * links to its page. An open submission has no page a stranger can open, so it
 * is named without a link.
 */

const CONFIRM_ID = "submission-confirm-not-duplicate";
export const DUPLICATE_HEADING_ID = "submission-duplicates-heading";

const MATCHED_ON: Record<DuplicateMatch["matched_on"], string> = {
  preferred_term: "its name",
  synonym: "another name",
  code: "its SNOMED CT code",
};

function MatchItem({ match }: { match: DuplicateMatch }) {
  const similarity =
    match.similarity === null ? "" : `, ${Math.round(match.similarity * 100)}% alike`;
  return (
    <li>
      {match.source === "catalogue_entry" ? (
        <Link to="/catalogue/$businessKey" params={{ businessKey: match.key }}>
          {match.key} — {match.preferred_term}
        </Link>
      ) : (
        <span>Submission already open — {match.preferred_term}</span>
      )}
      <span className="block text-sm text-[var(--color-text-muted)]">
        Matched on {MATCHED_ON[match.matched_on]}
        {match.matched_on === "synonym" ? ` "${match.term}"` : ""}
        {similarity}
      </span>
    </li>
  );
}

export function DuplicatePanel({
  matches,
  pending,
  error,
  headingRef,
  onConfirm,
  onBack,
}: {
  matches: DuplicateMatch[];
  pending: boolean;
  /** A refusal of the confirmed send that names no field, such as a used-up quota. */
  error?: string;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onConfirm: () => Promise<SubmitOutcome>;
  onBack: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const many = matches.length > 1;
  return (
    <section aria-labelledby={DUPLICATE_HEADING_ID} className="flex flex-col gap-4">
      <h2 id={DUPLICATE_HEADING_ID} ref={headingRef} tabIndex={-1}>
        This test may already exist
      </h2>
      <p className="m-0">
        {many ? "These records look" : "This record looks"} like the test you entered.
        Nothing has been submitted yet.
      </p>
      <ul className="m-0 flex flex-col gap-3 pl-5">
        {matches.map((match) => (
          <MatchItem
            key={`${match.source}-${match.key}-${match.matched_on}`}
            match={match}
          />
        ))}
      </ul>
      <Form
        submitLabel="Submit this test"
        pendingLabel="Submitting"
        pending={pending}
        formError={error}
        submitBlocked={!confirmed}
        blockedReason="Tick the box to say your test is different."
        blockedFieldId={CONFIRM_ID}
        errorSummaryHeadingLevel={3}
        onSubmit={onConfirm}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onBack}>
            Change my entry
          </Button>
        }
      >
        <Checkbox
          id={CONFIRM_ID}
          label={
            many
              ? "I have reviewed these records. My test is different from all of them."
              : "I have reviewed this record. My test is different."
          }
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
      </Form>
    </section>
  );
}
