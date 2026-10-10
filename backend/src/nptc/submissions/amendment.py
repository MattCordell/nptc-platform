"""Creating a submission that proposes a change to a published entry (FR-35, FR-27, FR-26, NFR-08).

An amendment names one `active` entry by business key and proposes new synonyms, a SNOMED CT code,
or both. Applying it to the entry is a later step; this function only records the proposal.

**Everything that can refuse runs before the first write.** The entry is read, the synonyms and free
text are cleaned, the code is resolved through the terminology server, and the reference link is
fetched. Only then does the function take the audit append lock, for the reason
`nptc.submissions.new_test` gives: the lock is global, so no request holds it across a network call.
`test_lock_ordering.py` names this function as exempt from its lock-first rule, and
`test_submissions_amendment.py` pins the ordering instead.

**Only an active entry can be amended.** A draft, deprecated or withdrawn entry raises
`AmendmentEntryNotActiveError`, which names the status. This differs from the public reads, which
answer 404 for any status but `active`, so a caller holding `amendment.propose` can learn that a
draft key exists.

**A change must change something.** A synonym the entry already holds is dropped, as a repeated
synonym is on a new test, and the amendment is refused if nothing is left to propose. A code equal to
the code the entry carries is refused too, because it proposes no change.

**An amendment carries no property values and takes no duplicate check.** The duplicate check
compares a proposed new test with the catalogue, and an amendment would match its own entry.

**The reference link is optional (FR-27).** A link that is given goes through the same check as on a
new test and is stored with the time and status the checker saw. A link left out stores all three
columns as null.

**`preferred_term` is the entry's, as it stood at the proposal.** The column is required on every
submission, so an amendment copies the entry's term. It is context for a reviewer, never a proposed
rename.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.designations import find_active_designation
from nptc.catalogue.entries import load_entry_for_update
from nptc.db.models.catalogue_entry import CatalogueEntryStatus
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM, CodeBinding, CodeBindingStatus
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.submissions.errors import (
    AmendmentEntryNotActiveError,
    AmendmentRefusal,
    AmendmentRefusedError,
    FreeTextField,
)
from nptc.submissions.reference_check import ReferenceChecker, ReferenceCheckResult
from nptc.submissions.shared import clean_free_text, resolve_code
from nptc.submissions.terms import distinct_synonyms
from nptc_shared.terminology import TerminologyClient

__all__ = ["AmendmentInput", "create_amendment_submission"]


@dataclass(frozen=True)
class AmendmentInput:
    """What a submitter supplies. There is no FSN field: it is served by the terminology server,
    never accepted (FR-82). `organisation` of `None` means "use the profile's"; a blank string
    means "none". `reference_url` is fetched as given when it is present."""

    entry_business_key: str
    synonyms: Sequence[str] = ()
    snomed_code: str | None = None
    reference_url: str | None = None
    notes: str | None = None
    organisation: str | None = None


def create_amendment_submission(
    session: Session,
    ctx: AuditContext,
    *,
    content: AmendmentInput,
    profile_organisation: str | None,
    terminology_client: TerminologyClient,
    reference_checker: ReferenceChecker,
) -> Submission:
    """Stores an amendment submission in state `Submitted`, attributed to `ctx.actor_user_id`, and
    appends its audit event.

    Raises `nptc.catalogue.errors.EntryNotFoundError` for a business key no entry has,
    `AmendmentEntryNotActiveError` for an entry that is not active, `AmendmentRefusedError` for an
    amendment that proposes no change, `nptc.catalogue.term_hygiene.TermCleaningError` for a synonym
    that is empty or carries an invisible character, `FreeTextRefusedError` for the same in `notes`
    or `organisation`, `nptc.terminology.errors` and `nptc_shared.sctid.InvalidSCTIDError` for a
    code that is malformed or cannot be looked up, `SubmissionCodeRefusedError` for a code the
    server knows but rules out, and `nptc.submissions.reference_check.ReferenceCheckFailedError` or
    `ReferenceCheckUnavailableError` for a reference link that did not pass. Each is raised before
    any row is added. Blocks for up to the checker's deadline when a reference link is given.
    """
    if ctx.actor_user_id is None:
        raise ValueError("a submission needs a human submitter, but the audit context has none")

    entry = load_entry_for_update(session, content.entry_business_key)
    if entry.status != CatalogueEntryStatus.ACTIVE:
        raise AmendmentEntryNotActiveError(CatalogueEntryStatus(entry.status))

    synonyms = [
        term
        for term in distinct_synonyms(entry.preferred_term, content.synonyms)
        if find_active_designation(session, entry_id=entry.id, term=term) is None
    ]
    notes = clean_free_text(content.notes, field=FreeTextField.NOTES, multiline=True)
    organisation = (
        profile_organisation
        if content.organisation is None
        else clean_free_text(
            content.organisation, field=FreeTextField.ORGANISATION, multiline=False
        )
    )

    if not synonyms and content.snomed_code is None:
        raise AmendmentRefusedError(
            AmendmentRefusal.NOTHING_NEW if content.synonyms else AmendmentRefusal.NOTHING_PROPOSED
        )

    snomed_code: str | None = None
    snomed_fsn: str | None = None
    if content.snomed_code is not None:
        snomed_code, snomed_fsn = resolve_code(terminology_client, content.snomed_code)
        if _entry_carries_code(session, entry.id, snomed_code):
            raise AmendmentRefusedError(AmendmentRefusal.CODE_ALREADY_BOUND)

    reference: ReferenceCheckResult | None = (
        None if content.reference_url is None else reference_checker.check(content.reference_url)
    )

    acquire_append_lock(session)
    submission = Submission(
        kind=SubmissionKind.AMENDMENT.value,
        state=SubmissionState.SUBMITTED.value,
        entry_id=entry.id,
        preferred_term=entry.preferred_term,
        synonyms=synonyms,
        snomed_code=snomed_code,
        snomed_fsn=snomed_fsn,
        property_values={},
        notes=notes,
        reference_url=content.reference_url,
        reference_checked_at=None if reference is None else reference.checked_at,
        reference_status=None if reference is None else reference.status,
        duplicate_confirmed_at=None,
        duplicate_matches=[],
        submitter_id=ctx.actor_user_id,
        organisation=organisation,
    )
    session.add(submission)
    record_change(
        session,
        ctx,
        action="submission.created",
        instance=submission,
        kind=ChangeKind.CREATED,
    )
    return submission


def _entry_carries_code(session: Session, entry_id: uuid.UUID, code: str) -> bool:
    """Whether `code` is the entry's active SNOMED CT binding. A retired binding is history, so
    proposing it again is a change."""
    return (
        session.execute(
            select(CodeBinding.id).where(
                CodeBinding.entry_id == entry_id,
                CodeBinding.system == SNOMED_CT_SYSTEM,
                CodeBinding.code == code,
                CodeBinding.status == CodeBindingStatus.ACTIVE,
            )
        ).first()
        is not None
    )
