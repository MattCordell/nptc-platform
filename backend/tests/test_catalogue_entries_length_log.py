"""FR-86 on the write path: `create_entry`, `save_entry` and `save_entries`
log one record when a written preferred term is over the configured maximum.

The principal failure mode is refusing the write, so every case also asserts
the term was stored. Records are filtered to this module's own logger, and
every entry is one this test created, so nothing depends on other tests'
rows (the shared-container convention).
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import EntryChanges, create_entry, save_entries, save_entry
from nptc.catalogue.errors import EntryVersionConflictError
from nptc.db.models.catalogue_entry import CatalogueEntry

_LOGGER = "nptc.catalogue.entries"
_REASON = "Created for the FR-86 write-path test"


@pytest.fixture
def session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == _LOGGER]


def _new_entry(session: Session, term: str, **kwargs: int | None) -> CatalogueEntry:
    entry = create_entry(
        session, AuditContext.system(), preferred_term=term, reason=_REASON, **kwargs
    )
    session.flush()
    return entry


def _save(
    session: Session, entry: CatalogueEntry, changes: EntryChanges, maximum: int | None
) -> CatalogueEntry:
    saved = save_entry(
        session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        changes=changes,
        reason=_REASON,
        max_preferred_term_length=maximum,
    )
    session.flush()
    return saved


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_creating_an_over_length_entry_logs_once_and_still_saves(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    term = "Adenosine deaminase, cerebrospinal fluid"

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        entry = _new_entry(session, term, max_preferred_term_length=len(term) - 1)

    records = _records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert entry.business_key in message
    assert str(len(term)) in message
    assert term not in message
    assert entry.preferred_term == term
    assert entry in session


@pytest.mark.req("FR-86")
@pytest.mark.integration
@pytest.mark.parametrize("offset", [0, 1], ids=["exactly-at-maximum", "under-maximum"])
def test_creating_an_entry_within_the_maximum_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture, offset: int
) -> None:
    term = "Full blood count"

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _new_entry(session, term, max_preferred_term_length=len(term) + offset)

    assert _records(caplog) == []


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_creating_an_entry_with_no_maximum_logs_nothing_however_long_the_term(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        entry = _new_entry(session, "A" * 500)

    assert _records(caplog) == []
    assert len(entry.preferred_term) == 500


@pytest.mark.req("FR-86")
@pytest.mark.req("FR-85")
@pytest.mark.integration
def test_the_logged_length_is_the_cleaned_length_not_the_raw_length(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    nbsp = chr(0x00A0)
    cleaned = "Full blood count"

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _new_entry(session, f"{cleaned}{nbsp}", max_preferred_term_length=len(cleaned))

    assert _records(caplog) == []


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_saving_a_longer_term_logs_once_and_still_saves(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _new_entry(session, "Iron")
    longer = "Full blood count, automated"

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        saved = _save(session, entry, EntryChanges(preferred_term=longer), maximum=10)

    records = _records(caplog)
    assert len(records) == 1
    assert saved.business_key in records[0].getMessage()
    assert longer not in records[0].getMessage()
    assert saved.preferred_term == longer


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_saving_a_term_exactly_at_the_maximum_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _new_entry(session, "Iron")
    term = "Full blood count"

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _save(session, entry, EntryChanges(preferred_term=term), maximum=len(term))

    assert _records(caplog) == []


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_saving_with_no_maximum_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _new_entry(session, "Iron")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        saved = _save(session, entry, EntryChanges(preferred_term="A" * 500), maximum=None)

    assert _records(caplog) == []
    assert len(saved.preferred_term) == 500


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_resubmitting_an_unchanged_over_length_term_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """A no-op save writes nothing, so there is no write to warn about."""
    term = "Full blood count, automated"
    entry = _new_entry(session, term)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _save(session, entry, EntryChanges(preferred_term=term), maximum=10)

    assert _records(caplog) == []


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_a_status_only_save_of_an_over_length_entry_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """The entry stays editable for its other columns: only a write of the
    preferred term is a write FR-86 is about."""
    entry = _new_entry(session, "Full blood count, automated")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        saved = _save(session, entry, EntryChanges(status="active"), maximum=10)

    assert _records(caplog) == []
    assert saved.status == "active"


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_a_batch_save_logs_one_record_per_over_length_term(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    short_entry = _new_entry(session, "Iron")
    long_entry = _new_entry(session, "Zinc")
    updates = [
        (short_entry.business_key, short_entry.row_version, EntryChanges(preferred_term="Copper")),
        (
            long_entry.business_key,
            long_entry.row_version,
            EntryChanges(preferred_term="Zinc, serum, quantitative"),
        ),
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        saved = save_entries(
            session,
            AuditContext.system(),
            updates=updates,
            reason=_REASON,
            max_preferred_term_length=10,
        )

    records = _records(caplog)
    assert len(records) == 1
    assert long_entry.business_key in records[0].getMessage()
    assert [entry.preferred_term for entry in saved] == ["Copper", "Zinc, serum, quantitative"]
    assert "more)" not in records[0].getMessage()


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_a_large_batch_logs_one_summary_record_naming_a_bounded_number_of_keys(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    entries = [_new_entry(session, f"Batch cap probe {n:02d}") for n in range(12)]
    updates = [
        (
            entry.business_key,
            entry.row_version,
            EntryChanges(preferred_term=f"{entry.preferred_term}, extended"),
        )
        for entry in entries
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        save_entries(
            session,
            AuditContext.system(),
            updates=updates,
            reason=_REASON,
            max_preferred_term_length=10,
        )

    records = _records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("12 preferred terms")
    named = [entry.business_key for entry in entries if entry.business_key in message]
    assert len(named) == 10
    assert message.endswith("... (2 more)")


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_a_batch_that_fails_part_way_logs_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """The record is emitted once every save has succeeded, so a batch that
    a later stale version refuses does not claim terms it will not keep."""
    first = _new_entry(session, "Iron")
    second = _new_entry(session, "Zinc")
    updates = [
        (first.business_key, first.row_version, EntryChanges(preferred_term="Full blood count")),
        (second.business_key, second.row_version + 1, EntryChanges(preferred_term="Copper")),
    ]

    with caplog.at_level(logging.WARNING, logger=_LOGGER), pytest.raises(EntryVersionConflictError):
        save_entries(
            session,
            AuditContext.system(),
            updates=updates,
            reason=_REASON,
            max_preferred_term_length=10,
        )

    assert _records(caplog) == []
