"""Migration 0023: the `specimen` binding includes the root (FR-88, FR-89, ADR-0044).

Runs on its own database, because it moves the schema back to 0022 and forward again. A
migration is the only way an existing database learns the new binding: `seed_system_properties`
inserts what is missing and never revisits a row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB = "nptc_specimen_binding"
_OLD_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
_NEW_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009"

_INSERT = text(
    "INSERT INTO property_definition (key, label, datatype, cardinality, scope, "
    "required_for_submission, required_for_publication, binding_target, value_set_uri, "
    "strength, edition, constraints, filterable, origin, display_order) "
    "VALUES (:key, 'Specimen', 'code', '0..*', 'both', false, false, 'value_set', :uri, "
    "'required', 'au', CAST(:constraints AS jsonb), true, :origin, 30)"
)


def _config() -> Config:
    return Config(toml_file=str(REPO_ROOT / "pyproject.toml"))


def _migrate(engine: Engine, direction: str, revision: str) -> None:
    config = _config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)
        connection.commit()


def _at_0022(engine: Engine) -> None:
    """Brings the database to exactly revision 0022, whichever side of it it is on."""
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    _migrate(engine, "downgrade" if current == "0023" else "upgrade", "0022")


@pytest.fixture(scope="module")
def engine(postgres_container: PostgresContainer, migrated: None) -> Iterator[Engine]:
    owner_url = make_url(postgres_container.get_connection_url())
    admin = create_engine(owner_url.render_as_string(hide_password=False))
    with admin.connect() as raw:
        connection = raw.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(f"DROP DATABASE IF EXISTS {_DB}"))
        connection.execute(text(f"CREATE DATABASE {_DB}"))
    admin.dispose()
    engine = create_engine(owner_url.set(database=_DB).render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()


def _at_0022_with(engine: Engine, *rows: dict[str, Any]) -> None:
    _at_0022(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM property_definition WHERE key = 'specimen'"))
        for row in rows or ({},):
            connection.execute(
                _INSERT,
                {
                    "key": "specimen",
                    "origin": "system",
                    "uri": _OLD_URI,
                    "constraints": json.dumps({"forbidden_codes": ["Any"]}),
                    **row,
                },
            )


def _specimen(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT value_set_uri, constraints, row_version FROM property_definition "
                "WHERE key = 'specimen'"
            )
        ).one()
    return {"uri": row[0], "constraints": row[1], "row_version": row[2]}


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_an_existing_specimen_definition_gets_the_widened_binding(engine: Engine) -> None:
    _at_0022_with(engine)

    _migrate(engine, "upgrade", "0023")

    specimen = _specimen(engine)
    assert specimen["uri"] == _NEW_URI
    assert specimen["constraints"] == {}


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_the_migration_leaves_row_version_alone(engine: Engine) -> None:
    """`row_version` has one write path, the ORM's, and a migration must not bump it."""
    _at_0022_with(engine)
    before = _specimen(engine)["row_version"]

    _migrate(engine, "upgrade", "0023")

    assert _specimen(engine)["row_version"] == before


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_other_constraints_survive_and_only_the_old_refusal_is_removed(engine: Engine) -> None:
    _at_0022_with(engine, {"constraints": json.dumps({"forbidden_codes": ["Any"], "kept": "yes"})})

    _migrate(engine, "upgrade", "0023")

    assert _specimen(engine)["constraints"] == {"kept": "yes"}


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_an_administrators_own_forbidden_codes_are_not_removed(engine: Engine) -> None:
    _at_0022_with(engine, {"constraints": json.dumps({"forbidden_codes": ["Any", "None"]})})

    _migrate(engine, "upgrade", "0023")

    specimen = _specimen(engine)
    assert specimen["uri"] == _NEW_URI
    assert specimen["constraints"] == {"forbidden_codes": ["Any", "None"]}


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_a_binding_an_administrator_changed_is_not_overwritten(engine: Engine) -> None:
    other = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C119376003"
    _at_0022_with(engine, {"uri": other})

    _migrate(engine, "upgrade", "0023")

    assert _specimen(engine)["uri"] == other


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_a_database_with_no_specimen_definition_upgrades_cleanly(engine: Engine) -> None:
    _at_0022_with(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM property_definition WHERE key = 'specimen'"))

    _migrate(engine, "upgrade", "0023")

    with engine.connect() as connection:
        remaining = connection.execute(
            text("SELECT count(*) FROM property_definition WHERE key = 'specimen'")
        ).scalar_one()
    assert remaining == 0


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_the_downgrade_restores_the_old_binding_and_refusal(engine: Engine) -> None:
    _at_0022_with(engine)
    _migrate(engine, "upgrade", "0023")

    _migrate(engine, "downgrade", "0022")

    specimen = _specimen(engine)
    assert specimen["uri"] == _OLD_URI
    assert specimen["constraints"] == {"forbidden_codes": ["Any"]}
