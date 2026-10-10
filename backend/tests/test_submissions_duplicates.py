"""`nptc.submissions.duplicates.find_duplicates` tests (FR-25).

Every term and key here is random, and every assertion is about rows this test created, because
`backend/tests` shares one Postgres container (see `CLAUDE.md`). A term is a run of random letters,
so a near match is built by changing one letter and no seeded row can match it by accident.
"""

from __future__ import annotations

import random
import string
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ClauseElement, Executable

from nptc.catalogue.queries import PUBLIC_STATUSES
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation, DesignationStatus
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission, SubmissionState
from nptc.db.models.user import User
from nptc.submissions import duplicates
from nptc.submissions.duplicates import (
    MAX_MATCHES_PER_SOURCE,
    DuplicateMatch,
    MatchedOn,
    MatchSource,
    find_duplicates,
)

_CODE = "391483001"
_OTHER_CODE = "71388002"
_FSN = "Microscopy (procedure)"


def _word(length: int = 14) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


def _near(term: str) -> str:
    """`term` with one more letter on the end: well above the 0.3 similarity threshold."""
    return f"{term}s"


def _business_key() -> str:
    return f"NPTC-{random.randrange(100_000_000, 999_999_999)}"


def _entry(
    session: Session,
    term: str,
    *,
    status: CatalogueEntryStatus = CatalogueEntryStatus.ACTIVE,
    synonyms: tuple[str, ...] = (),
    retired_synonyms: tuple[str, ...] = (),
    code: str | None = None,
) -> CatalogueEntry:
    entry = CatalogueEntry(business_key=_business_key(), preferred_term=term, status=status.value)
    session.add(entry)
    session.flush()
    session.add_all(Designation(entry_id=entry.id, term=synonym) for synonym in synonyms)
    session.add_all(
        Designation(
            entry_id=entry.id,
            term=synonym,
            status=DesignationStatus.RETIRED.value,
            retired_at=datetime.now(UTC),
        )
        for synonym in retired_synonyms
    )
    if code is not None:
        session.add(CodeBinding(entry_id=entry.id, code=code, fsn=_FSN))
    session.flush()
    return entry


def _submission(
    session: Session,
    term: str,
    *,
    synonyms: tuple[str, ...] = (),
    code: str | None = None,
) -> Submission:
    user = User(username=f"dup-{uuid.uuid4()}", display_name="Duplicate tester")
    session.add(user)
    session.flush()
    submission = Submission(
        kind="new_test",
        state=SubmissionState.SUBMITTED.value,
        preferred_term=term,
        synonyms=list(synonyms),
        snomed_code=code,
        snomed_fsn=_FSN if code is not None else None,
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=user.id,
    )
    session.add(submission)
    session.flush()
    return submission


def _find(
    session: Session,
    preferred_term: str,
    *,
    synonyms: tuple[str, ...] = (),
    code: str | None = None,
) -> list[DuplicateMatch]:
    return find_duplicates(
        session, preferred_term=preferred_term, synonyms=synonyms, snomed_code=code
    )


def _by_key(matches: list[DuplicateMatch], key: object) -> DuplicateMatch | None:
    return next((match for match in matches if match.key == str(key)), None)


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_an_entry_with_the_same_code_matches_whatever_its_name(owner_session: Session) -> None:
    entry = _entry(owner_session, _word(), code=_CODE)

    match = _by_key(_find(owner_session, _word(), code=_CODE), entry.business_key)

    assert match is not None
    assert match.source is MatchSource.CATALOGUE_ENTRY
    assert match.matched_on is MatchedOn.CODE
    assert match.similarity is None
    assert match.preferred_term == entry.preferred_term


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_an_open_submission_with_the_same_code_matches(owner_session: Session) -> None:
    submission = _submission(owner_session, _word(), code=_CODE)

    match = _by_key(_find(owner_session, _word(), code=_CODE), submission.id)

    assert match is not None
    assert match.source is MatchSource.SUBMISSION
    assert match.matched_on is MatchedOn.CODE


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_different_code_does_not_match(owner_session: Session) -> None:
    entry = _entry(owner_session, _word(), code=_CODE)
    submission = _submission(owner_session, _word(), code=_CODE)

    matches = _find(owner_session, _word(), code=_OTHER_CODE)

    assert _by_key(matches, entry.business_key) is None
    assert _by_key(matches, submission.id) is None


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_near_preferred_term_matches_an_entry(owner_session: Session) -> None:
    term = _word()
    entry = _entry(owner_session, term)

    match = _by_key(_find(owner_session, _near(term)), entry.business_key)

    assert match is not None
    assert match.matched_on is MatchedOn.PREFERRED_TERM
    assert match.term == term
    assert match.similarity is not None
    assert 0.3 <= match.similarity < 1


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_near_synonym_matches_the_entry_that_holds_it(owner_session: Session) -> None:
    synonym = _word()
    entry = _entry(owner_session, _word(), synonyms=(synonym,))

    match = _by_key(_find(owner_session, _near(synonym)), entry.business_key)

    assert match is not None
    assert match.matched_on is MatchedOn.SYNONYM
    assert match.term == synonym
    assert match.preferred_term == entry.preferred_term


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_submitted_synonym_is_compared_with_the_entries(owner_session: Session) -> None:
    term = _word()
    entry = _entry(owner_session, term)

    match = _by_key(_find(owner_session, _word(), synonyms=(_near(term),)), entry.business_key)

    assert match is not None
    assert match.matched_on is MatchedOn.PREFERRED_TERM


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_near_preferred_term_matches_an_open_submission(owner_session: Session) -> None:
    term = _word()
    submission = _submission(owner_session, term)

    match = _by_key(_find(owner_session, _near(term)), submission.id)

    assert match is not None
    assert match.source is MatchSource.SUBMISSION
    assert match.matched_on is MatchedOn.PREFERRED_TERM


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_near_synonym_matches_the_open_submission_that_holds_it(owner_session: Session) -> None:
    synonym = _word()
    submission = _submission(owner_session, _word(), synonyms=(synonym,))

    match = _by_key(_find(owner_session, _near(synonym)), submission.id)

    assert match is not None
    assert match.source is MatchSource.SUBMISSION
    assert match.matched_on is MatchedOn.SYNONYM
    assert match.term == synonym
    assert match.preferred_term == submission.preferred_term


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_term_with_nothing_similar_has_no_match(owner_session: Session) -> None:
    _entry(owner_session, _word(), synonyms=(_word(),))
    _submission(owner_session, _word(), synonyms=(_word(),))

    assert _find(owner_session, _word(20), synonyms=(_word(20),)) == []


@pytest.mark.req("FR-25")
@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [CatalogueEntryStatus.DRAFT, CatalogueEntryStatus.DEPRECATED, CatalogueEntryStatus.WITHDRAWN],
)
def test_an_entry_the_public_cannot_see_does_not_match(
    owner_session: Session, status: CatalogueEntryStatus
) -> None:
    term = _word()
    entry = _entry(owner_session, term, status=status, synonyms=(_near(term),), code=_CODE)

    matches = _find(owner_session, term, code=_CODE)

    assert _by_key(matches, entry.business_key) is None


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_retired_synonym_does_not_match(owner_session: Session) -> None:
    retired = _word()
    entry = _entry(owner_session, _word(), retired_synonyms=(retired,))

    assert _by_key(_find(owner_session, retired), entry.business_key) is None


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_the_public_statuses_are_the_active_status_alone() -> None:
    assert (CatalogueEntryStatus.ACTIVE.value,) == PUBLIC_STATUSES


@pytest.mark.req("FR-25")
def test_a_closed_state_is_one_a_proposal_has_left() -> None:
    assert set(CLOSED_SUBMISSION_STATES) == {"Published in release", "Rejected", "Withdrawn"}
    assert SubmissionState.SUBMITTED.value not in CLOSED_SUBMISSION_STATES


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_only_a_submission_in_a_closed_state_stops_matching(owner_session: Session) -> None:
    """The `state` check allows only `Submitted` until the workflow states arrive, so the check is
    dropped inside this test's rolled-back transaction to store the states the filter is about."""
    owner_session.execute(text("ALTER TABLE submission DROP CONSTRAINT ck_submission_state"))
    term = _word()
    states = [*CLOSED_SUBMISSION_STATES, "Submitted", "Under review"]
    submissions = {state: _submission(owner_session, term) for state in states}
    for state, submission in submissions.items():
        owner_session.execute(
            text("UPDATE submission SET state = :state WHERE id = :id"),
            {"state": state, "id": submission.id},
        )

    matches = _find(owner_session, term)

    matched_states = {state for state, row in submissions.items() if _by_key(matches, row.id)}
    assert matched_states == {"Submitted", "Under review"}


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_record_that_matches_twice_appears_once_on_its_best_match(
    owner_session: Session,
) -> None:
    term = _word()
    entry = _entry(owner_session, term, synonyms=(_near(term),), code=_CODE)

    matches = [
        match
        for match in _find(owner_session, term, synonyms=(_near(term),), code=_CODE)
        if match.key == entry.business_key
    ]

    assert [match.matched_on for match in matches] == [MatchedOn.CODE]


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_matches_are_capped_per_source_and_best_first(owner_session: Session) -> None:
    term = _word()
    for _ in range(MAX_MATCHES_PER_SOURCE + 2):
        _entry(owner_session, _near(term))
    _submission(owner_session, _near(term))

    matches = _find(owner_session, term)

    entries = [match for match in matches if match.source is MatchSource.CATALOGUE_ENTRY]
    scores = [match.similarity for match in entries]
    assert len(entries) == MAX_MATCHES_PER_SOURCE
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]
    assert any(match.source is MatchSource.SUBMISSION for match in matches)


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_the_stored_record_names_the_match_and_not_its_text(owner_session: Session) -> None:
    term = _word()
    entry = _entry(owner_session, term)

    match = _by_key(_find(owner_session, _near(term)), entry.business_key)

    assert match is not None
    assert set(match.as_record()) == {"source", "key", "matched_on", "similarity"}
    assert term not in str(match.as_record().values())


class _Explain(Executable, ClauseElement):
    inherit_cache = False

    def __init__(self, statement: ClauseElement) -> None:
        self.statement = statement


@compiles(_Explain)
def _compile_explain(element: _Explain, compiler: object, **kw: object) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)  # type: ignore[attr-defined]


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_the_catalogue_side_is_served_by_the_trigram_indexes(owner_session: Session) -> None:
    """`enable_seqscan = off` removes the planner's cost choice, so a predicate the index cannot
    serve, such as `similarity(...) > 0.3`, shows as a `Filter` and fails here. See
    `test_db_search_index.py` for why this is not cheating."""
    for _ in range(50):
        _entry(owner_session, _word(), synonyms=(_word(),))
    owner_session.execute(text("ANALYZE catalogue_entry"))
    owner_session.execute(text("ANALYZE designation"))
    owner_session.execute(text("SET LOCAL enable_seqscan = off"))
    owner_session.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', '0.3', true)"),
    )

    plan = "\n".join(
        owner_session.execute(
            _Explain(duplicates._DUPLICATES_SQL),
            {
                "terms": [_word()],
                "code": _CODE,
                "statuses": list(PUBLIC_STATUSES),
                "closed_states": list(CLOSED_SUBMISSION_STATES),
                "per_source": MAX_MATCHES_PER_SOURCE,
            },
        )
        .scalars()
        .all()
    )

    assert "ix_catalogue_entry_preferred_term_trgm" in plan, plan
    assert "ix_designation_term_trgm" in plan, plan
