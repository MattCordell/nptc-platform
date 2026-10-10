"""`nptc.submissions.amendment.create_amendment_submission` service-layer tests (FR-35, FR-26, FR-27,
NFR-08).

Each refusal asserts that no `submission` row and no audit event was added, because a refused
request must leave nothing behind. Rows are counted for the submitter this test created, and audit
events as a delta, because `backend/tests` shares one Postgres container (see `CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import random
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.errors import EntryNotFoundError
from nptc.catalogue.term_hygiene import TermCleaningError
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding, CodeBindingStatus
from nptc.db.models.designation import Designation as DesignationRow
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.user import User
from nptc.submissions.amendment import AmendmentInput, create_amendment_submission
from nptc.submissions.errors import (
    AmendmentEntryNotActiveError,
    AmendmentRefusal,
    AmendmentRefusedError,
    CodeRefusal,
    FreeTextRefusedError,
    SubmissionCodeRefusedError,
)
from nptc.submissions.reference_check import (
    ReferenceCheckFailedError,
    ReferenceCheckResult,
    ReferenceFailure,
)
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyStatusError,
)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


StubReferenceChecker = _load("api_app_support").StubReferenceChecker

_REFERENCE_URL = "https://example.org/evidence"
_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_OTHER_CODE = "119364003"
_PROFILE_ORGANISATION = "Profile Pathology"
ZERO_WIDTH_SPACE = chr(0x200B)


def _client(*, active: bool = True) -> StubTerminologyClient:
    client = StubTerminologyClient()
    client.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            active=active,
        )
    )
    return client


def _submitter(session: Session) -> tuple[User, AuditContext]:
    user = User(
        username=f"submitter-{uuid.uuid4()}",
        display_name="Test Submitter",
        organisation=_PROFILE_ORGANISATION,
    )
    session.add(user)
    session.flush()
    ctx = AuditContext(
        actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )
    return user, ctx


def _entry(
    session: Session,
    *,
    term: str = "Serum sodium",
    status: CatalogueEntryStatus = CatalogueEntryStatus.ACTIVE,
    synonyms: tuple[str, ...] = (),
) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
        preferred_term=term,
        status=status.value,
    )
    session.add(entry)
    session.flush()
    for synonym in synonyms:
        session.add(DesignationRow(entry_id=entry.id, term=synonym))
    session.flush()
    return entry


def _bind(session: Session, entry: CatalogueEntry, *, status: CodeBindingStatus) -> None:
    session.add(
        CodeBinding(
            entry_id=entry.id,
            code=_CODE,
            fsn=_FSN,
            au_preferred_term=None,
            edition_hint="au",
            status=status.value,
            retired_at=None if status is CodeBindingStatus.ACTIVE else datetime.now(UTC),
            retirement_reason=(
                None if status is CodeBindingStatus.ACTIVE else "Concept inactivated upstream."
            ),
        )
    )
    session.flush()


def _submission_count(session: Session, submitter_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == submitter_id)
    ).scalar_one()


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _create(
    session: Session,
    ctx: AuditContext,
    content: AmendmentInput,
    client: StubTerminologyClient | None = None,
    *,
    profile_organisation: str | None = _PROFILE_ORGANISATION,
    reference_checker: Any = None,
) -> Submission:
    return create_amendment_submission(
        session,
        ctx,
        content=content,
        profile_organisation=profile_organisation,
        terminology_client=client if client is not None else StubTerminologyClient(),
        reference_checker=reference_checker
        if reference_checker is not None
        else StubReferenceChecker(),
    )


def _assert_nothing_saved(
    session: Session, user: User, audit_events_before: int, checker: Any = None
) -> None:
    assert _submission_count(session, user.id) == 0
    assert _audit_event_count(session) == audit_events_before


# --- the stored record -------------------------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_synonym_amendment_is_stored_against_the_entry(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
    )

    assert submission.kind == SubmissionKind.AMENDMENT
    assert submission.state == SubmissionState.SUBMITTED
    assert submission.entry_id == entry.id
    assert submission.submitter_id == user.id
    assert submission.preferred_term == entry.preferred_term
    assert submission.synonyms == ["Na (serum)"]
    assert submission.snomed_code is None
    assert submission.property_values == {}
    assert submission.duplicate_matches == []
    assert submission.duplicate_confirmed_at is None
    assert submission.organisation == _PROFILE_ORGANISATION


@pytest.mark.req("FR-35")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_amendment_stores_the_code_and_the_fsn_the_server_returned(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, snomed_code=_CODE),
        _client(),
    )

    assert submission.snomed_code == _CODE
    assert submission.snomed_fsn == _FSN
    assert submission.synonyms == []


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_can_carry_a_synonym_a_code_and_notes(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(
            entry_business_key=entry.business_key,
            synonyms=["Na (serum)"],
            snomed_code=_CODE,
            notes="  Seen on the new analyser panel  ",
            organisation="Other Pathology",
        ),
        _client(),
    )

    assert submission.synonyms == ["Na (serum)"]
    assert submission.snomed_code == _CODE
    assert submission.notes == "Seen on the new analyser panel"
    assert submission.organisation == "Other Pathology"


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_synonym_the_entry_already_has_is_dropped_and_the_rest_kept(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session, synonyms=("Sodium, serum",))

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(
            entry_business_key=entry.business_key,
            synonyms=["sodium serum", "SERUM SODIUM", "Na (serum)"],
        ),
    )

    assert submission.synonyms == ["Na (serum)"]


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_with_no_organisation_given_uses_the_profile_value(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
        profile_organisation="Another profile",
    )

    assert submission.organisation == "Another profile"


# --- the entry --------------------------------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_unknown_business_key_is_refused_and_saves_nothing(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(EntryNotFoundError):
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key="NPTC-000000000", synonyms=["Na (serum)"]),
        )

    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [
        CatalogueEntryStatus.DRAFT,
        CatalogueEntryStatus.DEPRECATED,
        CatalogueEntryStatus.WITHDRAWN,
    ],
)
def test_an_entry_that_is_not_active_is_refused_with_its_status(
    app_session: Session, status: CatalogueEntryStatus
) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session, status=status)
    checker = StubReferenceChecker()
    before = _audit_event_count(app_session)

    with pytest.raises(AmendmentEntryNotActiveError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                synonyms=["Na (serum)"],
                reference_url=_REFERENCE_URL,
            ),
            reference_checker=checker,
        )

    assert excinfo.value.status == status
    _assert_nothing_saved(app_session, user, before)
    assert checker.urls == []


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_needs_a_human_submitter(app_session: Session) -> None:
    entry = _entry(app_session)
    ctx = AuditContext.system()

    with pytest.raises(ValueError, match="human submitter"):
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
        )


# --- an amendment must change something -------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_with_no_synonym_and_no_code_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(AmendmentRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, notes="Only a note"),
        )

    assert excinfo.value.reason == AmendmentRefusal.NOTHING_PROPOSED
    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_synonyms_the_entry_already_has_leave_nothing_to_propose(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session, synonyms=("Sodium, serum",))
    before = _audit_event_count(app_session)

    with pytest.raises(AmendmentRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                synonyms=["Sodium, serum", entry.preferred_term],
            ),
        )

    assert excinfo.value.reason == AmendmentRefusal.NOTHING_NEW
    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_retired_synonym_can_be_proposed_again(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    app_session.add(
        DesignationRow(
            entry_id=entry.id, term="Na (serum)", status="retired", retired_at=datetime.now(UTC)
        )
    )
    app_session.flush()

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
    )

    assert submission.synonyms == ["Na (serum)"]


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_code_the_entry_already_carries_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    _bind(app_session, entry, status=CodeBindingStatus.ACTIVE)
    before = _audit_event_count(app_session)

    with pytest.raises(AmendmentRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                synonyms=["Na (serum)"],
                snomed_code=_CODE,
            ),
            _client(),
        )

    assert excinfo.value.reason == AmendmentRefusal.CODE_ALREADY_BOUND
    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_code_the_entry_once_carried_can_be_proposed_again(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    _bind(app_session, entry, status=CodeBindingStatus.RETIRED)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, snomed_code=_CODE),
        _client(),
    )

    assert submission.snomed_code == _CODE


# --- the code (FR-26) -------------------------------------------------------------------------


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_inactive_code_is_refused_and_saves_nothing(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, snomed_code=_CODE),
            _client(active=False),
        )

    assert excinfo.value.reason == CodeRefusal.INACTIVE
    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_refused_and_saves_nothing(
    app_session: Session,
) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("not found", status_code=404))
    before = _audit_event_count(app_session)

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, snomed_code=_OTHER_CODE),
            client,
        )

    assert excinfo.value.reason == CodeRefusal.NOT_FOUND
    _assert_nothing_saved(app_session, user, before)


# --- free text and terms ----------------------------------------------------------------------


@pytest.mark.req("FR-63")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_invisible_character_in_the_notes_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(FreeTextRefusedError):
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                synonyms=["Na (serum)"],
                notes="a" + ZERO_WIDTH_SPACE + "b",
            ),
        )

    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-63")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_blank_synonym_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(TermCleaningError):
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, synonyms=["   "]),
        )

    _assert_nothing_saved(app_session, user, before)


# --- the optional reference link (FR-27) -------------------------------------------------------


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_without_a_reference_stores_all_three_columns_as_null(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    checker = StubReferenceChecker()

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
        reference_checker=checker,
    )

    assert submission.reference_url is None
    assert submission.reference_checked_at is None
    assert submission.reference_status is None
    assert checker.urls == []


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_reference_that_is_given_is_checked_and_stored_with_the_checkers_status(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    checker = StubReferenceChecker()
    checker.status = 403

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(
            entry_business_key=entry.business_key,
            synonyms=["Na (serum)"],
            reference_url=_REFERENCE_URL,
        ),
        reference_checker=checker,
    )

    assert checker.urls == [_REFERENCE_URL]
    assert submission.reference_url == _REFERENCE_URL
    assert submission.reference_status == 403
    assert submission.reference_checked_at is not None


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_reference_that_fails_its_check_saves_nothing(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    entry = _entry(app_session)
    checker = StubReferenceChecker()
    checker.error = ReferenceCheckFailedError(ReferenceFailure.BAD_STATUS, status=404)
    before = _audit_event_count(app_session)

    with pytest.raises(ReferenceCheckFailedError):
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                synonyms=["Na (serum)"],
                reference_url=_REFERENCE_URL,
            ),
            reference_checker=checker,
        )

    _assert_nothing_saved(app_session, user, before)


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_cheaper_refusal_means_no_link_is_fetched(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    checker = StubReferenceChecker()

    with pytest.raises(AmendmentRefusedError):
        _create(
            app_session,
            ctx,
            AmendmentInput(entry_business_key=entry.business_key, reference_url=_REFERENCE_URL),
            reference_checker=checker,
        )

    assert checker.urls == []


# --- the audit event (NFR-08) -----------------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_the_audit_event_records_the_kind_and_the_entry_link(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    before = _audit_event_count(app_session)

    submission = _create(
        app_session,
        ctx,
        AmendmentInput(entry_business_key=entry.business_key, synonyms=["Na (serum)"]),
    )

    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "submission.created"
    assert event.after is not None
    assert event.after["kind"] == SubmissionKind.AMENDMENT
    assert event.after["entry_id"] == str(entry.id)
    assert event.after["synonyms"] == ["Na (serum)"]
    assert event.entity_id == str(submission.id)


@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_no_network_call_is_made_while_the_append_lock_is_held(
    app_session: Session, capture_statements: Any
) -> None:
    """The lock is one global advisory lock, so a terminology or reference call made while holding
    it would stall every audit write. The lock-ordering guard exempts this writer because it must
    not take the lock first, so this test is what pins the order."""
    _, ctx = _submitter(app_session)
    entry = _entry(app_session)
    calls: list[tuple[str, bool]] = []

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    class _RecordingChecker(StubReferenceChecker):
        def check(self, url: str) -> ReferenceCheckResult:
            calls.append(("reference", bool(locks)))
            return super().check(url)

    class _RecordingClient(StubTerminologyClient):
        def lookup(self, *args: Any, **kwargs: Any) -> Any:
            calls.append(("lookup", bool(locks)))
            return super().lookup(*args, **kwargs)

    with capture_statements(app_session.get_bind(), keep=is_lock) as locks:
        client = _RecordingClient()
        client.add_concept(
            StubConcept(
                code=_CODE,
                fsn=_FSN,
                preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            )
        )
        _create(
            app_session,
            ctx,
            AmendmentInput(
                entry_business_key=entry.business_key,
                snomed_code=_CODE,
                reference_url=_REFERENCE_URL,
            ),
            client,
            reference_checker=_RecordingChecker(),
        )

    assert {name for name, _ in calls} == {"lookup", "reference"}
    assert [held for _, held in calls] == [False, False]
    # The probe does see the lock the write takes, so the assertions above cannot pass vacuously.
    assert locks
