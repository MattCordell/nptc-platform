"""business_key minting, format, and never-reused tests (issue #46, FR-03).

Uses an ORM `Session` bound to `app_db` (a single connection authenticated
as `nptc_app_login`, in an outer transaction rolled back after the test) -
`nptc.catalogue.entries` is meant to run as the app role in production, so
these tests exercise it under the same privilege set.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import (
    BUSINESS_KEY_SEQUENCE_NAME,
    EntryChanges,
    advance_sequence_past,
    allocate_business_key,
    create_entry,
    format_business_key,
    save_entry,
)
from nptc.db.models.catalogue_entry import CatalogueEntryStatus


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def test_format_business_key_is_nptc_plus_six_zero_padded_digits() -> None:
    assert format_business_key(1) == "NPTC-000001"
    assert format_business_key(247) == "NPTC-000247"
    # Not capped at six digits - see the function's own docstring.
    assert format_business_key(1_000_000) == "NPTC-1000000"


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_allocate_business_key_mints_sequential_keys(app_session: Session) -> None:
    first = allocate_business_key(app_session)
    second = allocate_business_key(app_session)

    assert first != second
    assert first.startswith("NPTC-")
    assert second.startswith("NPTC-")
    assert int(second[len("NPTC-") :]) == int(first[len("NPTC-") :]) + 1


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_create_entry_mints_a_key_when_none_supplied(app_session: Session) -> None:
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Full blood count",
        reason="Created for FR-03 minting test",
    )

    assert entry.business_key.startswith("NPTC-")


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_create_entry_accepts_an_explicit_seeded_key(app_session: Session) -> None:
    """ADR-0010: the P0 transform mints its own keys for a seeded row - the
    backend must accept one rather than always minting."""
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Full blood count",
        business_key="NPTC-000042",
        reason="Created for FR-03 seeded-key test",
    )

    assert entry.business_key == "NPTC-000042"


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_advance_sequence_past_prevents_a_collision_with_seeded_keys(
    app_session: Session,
) -> None:
    # A sequence is not transactional (Postgres exempts it from MVCC/
    # rollback so nextval() never blocks on a concurrent transaction), so
    # earlier tests in this same session may already have advanced it -
    # a fixed literal like "NPTC-000100" would make this test's pass/fail
    # depend on suite ordering. Establishing a fresh baseline first and
    # asserting only relative to it keeps the test deterministic
    # regardless of what the shared sequence's absolute value happens to
    # be when this test runs.
    baseline = int(allocate_business_key(app_session)[len("NPTC-") :])
    seeded_key = format_business_key(baseline + 1000)
    create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Seeded entry",
        business_key=seeded_key,
        reason="Created for FR-03 sequence-reconciliation test",
    )

    advance_sequence_past(app_session, seeded_key)

    next_minted = allocate_business_key(app_session)
    assert int(next_minted[len("NPTC-") :]) > baseline + 1000


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_advance_sequence_past_never_moves_the_sequence_backwards(
    app_session: Session,
) -> None:
    baseline = int(allocate_business_key(app_session)[len("NPTC-") :])
    advance_sequence_past(app_session, format_business_key(baseline + 1000))
    high_watermark = int(allocate_business_key(app_session)[len("NPTC-") :])

    # A stale (lower) reconciliation call must be a no-op, never reissuing
    # a key already minted since the last one.
    advance_sequence_past(app_session, format_business_key(baseline))
    next_minted = int(allocate_business_key(app_session)[len("NPTC-") :])

    assert next_minted > high_watermark


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_advance_sequence_past_reconciles_a_never_called_sequence(db: Connection) -> None:
    """Regression test for the off-by-one this function once had: Postgres
    reports `last_value = 1` for a freshly created sequence even though
    nothing has ever been dispensed from it (`is_called = false`) -
    comparing against `last_value` alone therefore cannot distinguish
    "never called" from "1 was issued", and treated reconciling a baseline
    as small as `NPTC-000001` as a no-op, letting the very next mint
    reissue that exact key. Every other test in this module calls
    `allocate_business_key` before `advance_sequence_past`, which sets
    `is_called` and hid this - this test drops and recreates the real
    sequence (DDL, unlike `nextval`/`setval`, fully participates in and
    rolls back with `db`'s own transaction) so it starts genuinely
    never-called, and uses the *owner* connection throughout rather than
    `app_db`, since the bug is in the SQL logic, not the privilege grant -
    owner and app-role sessions run the identical code path here."""
    db.execute(text("DROP SEQUENCE catalogue_entry_business_key_seq"))
    db.execute(text("CREATE SEQUENCE catalogue_entry_business_key_seq AS BIGINT START 1"))
    owner_session = Session(bind=db, join_transaction_mode="create_savepoint")

    advance_sequence_past(owner_session, "NPTC-000001")
    next_minted = allocate_business_key(owner_session)

    assert next_minted != "NPTC-000001"
    assert int(next_minted[len("NPTC-") :]) > 1


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_business_key_sequence_name_matches_the_migration(app_session: Session) -> None:
    """A single source of truth for the sequence name, asserted rather
    than merely hoped for - see `nptc.catalogue.entries.
    BUSINESS_KEY_SEQUENCE_NAME`'s own comment."""
    exists = app_session.execute(
        text("SELECT 1 FROM pg_sequences WHERE sequencename = :name"),
        {"name": BUSINESS_KEY_SEQUENCE_NAME},
    ).scalar_one_or_none()
    assert exists == 1


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_a_withdrawn_entrys_key_is_never_reissued(app_session: Session) -> None:
    entry = create_entry(
        app_session, AuditContext.system(), preferred_term="Old test", reason="Seeded for test"
    )
    withdrawn_key = entry.business_key

    save_entry(
        app_session,
        AuditContext.system(),
        business_key=withdrawn_key,
        expected_row_version=entry.row_version,
        changes=EntryChanges(status=CatalogueEntryStatus.WITHDRAWN.value),
        reason="withdrawing",
    )

    new_entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="New test",
        reason="Created after withdrawal for FR-03 reuse test",
    )

    assert new_entry.business_key != withdrawn_key
