"""`nptc.registry.bootstrap.seed_system_properties` tests (issue #51).

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_optimistic_locking.py`'s own `app_session` fixture for why
`join_transaction_mode="create_savepoint"` is needed: it lets the ORM's
own `commit()` calls nest inside the outer test transaction that
`app_db`'s connection fixture rolls back at teardown.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin
from nptc.registry.bootstrap import seed_system_properties


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
    count = app_session.scalar(
        select(PropertyDefinition.id).where(PropertyDefinition.origin == "system")
    )
    assert count is not None
    rows = app_session.scalars(
        select(PropertyDefinition).where(PropertyDefinition.origin == "system")
    ).all()
    assert len(rows) == 4


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

    rows = app_session.scalars(select(PropertyDefinition).order_by(PropertyDefinition.key)).all()
    origins = {row.key: row.origin for row in rows}
    assert origins["discipline"] == "system"
    assert origins["admin_defined"] == "admin"
    # Same table, same columns, same constraints applied to both - there is
    # no separate "system property" table or code path to diverge from.
    assert {type(row) for row in rows} == {PropertyDefinition}
