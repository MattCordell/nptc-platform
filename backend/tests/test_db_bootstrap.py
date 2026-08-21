"""`nptc.db.bootstrap.seed_system_properties` tests (issue #51).

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_optimistic_locking.py`'s own `app_session` fixture for why
`join_transaction_mode="create_savepoint"` is needed: it lets the ORM's
own `commit()` calls nest inside the outer test transaction that
`app_db`'s connection fixture rolls back at teardown.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_seeds_the_four_built_in_properties(app_session: Session) -> None:
    inserted = seed_system_properties(app_session)
    app_session.commit()

    assert set(inserted) == {"discipline", "subgroup", "specimen", "usage_guidance"}
    # Scoped to the four keys this test itself just inserted, not the whole
    # table - another test (or a prior seeding call sharing this container)
    # may have written other rows (issue #190's no-absolute-table-state rule).
    rows = app_session.scalars(
        select(PropertyDefinition)
        .where(PropertyDefinition.key.in_(inserted))
        .order_by(PropertyDefinition.display_order)
    ).all()
    assert [row.key for row in rows] == ["discipline", "subgroup", "specimen", "usage_guidance"]
    assert all(row.origin == PropertyOrigin.SYSTEM for row in rows)


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_is_idempotent_on_a_repeat_call(app_session: Session) -> None:
    """FR-09's own acceptance test starts here: seeding must be safely
    re-runnable with no migration and no restart between calls."""
    first = seed_system_properties(app_session)
    app_session.commit()
    assert len(first) == 4

    second = seed_system_properties(app_session)
    app_session.commit()

    assert second == []
    # `len(rows) == 4` would be tautological here - `first` already holds
    # four keys and `uq_property_definition_key` makes more than four
    # impossible, so it can't fail independently of `second == []` above.
    # Assert the set of keys instead, confirming the repeat call didn't
    # replace any row under a different identity. Scoped to the keys
    # seeding itself owns, not every origin='system' row in the table
    # (issue #190's no-absolute-table-state rule).
    rows = app_session.scalars(
        select(PropertyDefinition).where(PropertyDefinition.key.in_(first))
    ).all()
    assert {row.key for row in rows} == set(first)


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_specimen_is_bound_to_the_snomed_value_set(app_session: Session) -> None:
    seed_system_properties(app_session)
    app_session.commit()

    specimen = app_session.scalar(
        select(PropertyDefinition).where(PropertyDefinition.key == "specimen")
    )
    assert specimen is not None
    assert specimen.datatype == "code"
    assert specimen.binding_target == "value_set"
    assert specimen.value_set_uri == "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_system_properties_travel_the_same_write_path_as_an_admin_property(
    app_session: Session,
) -> None:
    """This issue's own AC: no special-casing beyond the `origin` column
    value itself - inserting an admin-defined property uses exactly the
    same `PropertyDefinition` constructor and the same `Session.add`/
    `commit` sequence `seed_system_properties` uses."""
    seed_system_properties(app_session)
    admin_property = PropertyDefinition(
        key="admin_defined",
        label="Admin defined",
        datatype="string",
        cardinality="0..1",
        scope="both",
        required_for_submission=False,
        required_for_publication=False,
        filterable=False,
        origin="admin",
        display_order=99,
    )
    app_session.add(admin_property)
    app_session.commit()

    # Scoped to the two keys this test itself wrote, not the whole table
    # (issue #190) - the load-bearing assertion is that both origins read
    # back correctly from one shared table via one shared code path, with
    # nothing else to check: there is no separate "system property" table
    # or query to diverge from in the first place.
    rows = app_session.scalars(
        select(PropertyDefinition).where(
            PropertyDefinition.key.in_(["discipline", "admin_defined"])
        )
    ).all()
    origins = {row.key: row.origin for row in rows}
    assert origins == {"discipline": "system", "admin_defined": "admin"}


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_seed_system_properties_is_safe_under_a_concurrent_caller(
    app_engine: Engine, owner_engine: Engine
) -> None:
    """Reproduces the actual race `seed_system_properties`'s per-row
    `SAVEPOINT` exists to handle - two independent sessions that both see
    every key missing, not merely a serial repeat call
    (`test_is_idempotent_on_a_repeat_call` already covers that).

    Both connections run `REPEATABLE READ`, and a bare `SELECT 1` on each
    fixes its snapshot before either has written anything - Postgres
    takes a `REPEATABLE READ` snapshot at the transaction's first
    statement, not at `BEGIN`. Session A then seeds and commits for real.
    Session B's own snapshot predates that commit, so its existing-keys
    `SELECT` still reports every key missing, exactly as a second real
    concurrent process's would; each of its four `INSERT`s then collides
    with a row A already committed - Postgres detects this write-write
    conflict against the committed row regardless of B's read snapshot,
    the same first-committer-wins behaviour two genuinely concurrent
    processes would hit. Real commits land in the shared container, so a
    `finally` block cleans them up via `owner_engine` (issue #190).
    """
    connection_a = app_engine.connect().execution_options(isolation_level="REPEATABLE READ")
    connection_b = app_engine.connect().execution_options(isolation_level="REPEATABLE READ")
    try:
        session_a = Session(bind=connection_a)
        session_b = Session(bind=connection_b)
        session_a.execute(text("SELECT 1"))
        session_b.execute(text("SELECT 1"))

        inserted_a = seed_system_properties(session_a)
        session_a.commit()
        assert set(inserted_a) == {"discipline", "subgroup", "specimen", "usage_guidance"}

        inserted_b = seed_system_properties(session_b)
        # The caught IntegrityError's SAVEPOINT rollback must expunge the
        # losing instance from the session - otherwise this commit would
        # try to flush the same doomed INSERT a second time, outside the
        # savepoint that caught it the first time, and raise.
        assert len(session_b.new) == 0
        session_b.commit()

        assert inserted_b == []
    finally:
        connection_a.close()
        connection_b.close()
        with owner_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                text(
                    "DELETE FROM property_definition WHERE key IN "
                    "('discipline', 'subgroup', 'specimen', 'usage_guidance')"
                )
            )
            cleanup_connection.commit()
