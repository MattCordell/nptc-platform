"""Migrations 0023 and 0024: the `specimen` binding includes the root, and the
`specimen_unconstrained` flag is retired (FR-88, FR-89, ADR-0044).

Runs on its own database, because it moves the schema back and forward. A migration is the only
way an existing database learns the new binding: `seed_system_properties` inserts what is
missing and never revisits a row.
"""

from __future__ import annotations

import json
import logging
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

from nptc.db.migration_guards import ForeignCodeOnPropertyValueError

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


def _at(engine: Engine, revision: str) -> None:
    """Brings the database to exactly `revision`, whichever side of it it is on. Revisions are
    zero-padded, so a string comparison orders them."""
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current is None or current < revision:
        _migrate(engine, "upgrade", revision)
    elif current > revision:
        _migrate(engine, "downgrade", revision)


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
    _at(engine, "0022")
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


# -- 0024: the flag converts, then goes ----------------------------------------------------------

_ROOT = "123038009"
_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term, preferred_term_key, "
    "specimen_unconstrained) VALUES (:key, :term, :term, :flag)"
)
_VALUE = text(
    "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
    "SELECT id, 'specimen', :ordinal, CAST(:value AS jsonb) FROM catalogue_entry "
    "WHERE business_key = :key"
)


def _coded(code: str) -> str:
    return json.dumps({"system": "http://snomed.info/sct", "code": code})


def _at_0023_with(
    engine: Engine,
    entries: dict[str, bool],
    specimens: dict[str, list[str]] | None = None,
    *,
    definition: bool = True,
) -> None:
    """The schema at 0023 holding `entries` (key -> flag) and their specimen codes."""
    _at(engine, "0023")
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM property_value"))
        connection.execute(text("DELETE FROM catalogue_entry"))
        connection.execute(text("DELETE FROM property_definition WHERE key = 'specimen'"))
        if definition:
            connection.execute(
                _INSERT,
                {
                    "key": "specimen",
                    "origin": "system",
                    "uri": _NEW_URI,
                    "constraints": "{}",
                },
            )
        for key, flag in entries.items():
            connection.execute(_ENTRY, {"key": key, "term": key, "flag": flag})
        for key, codes in (specimens or {}).items():
            for ordinal, code in enumerate(codes):
                connection.execute(_VALUE, {"key": key, "ordinal": ordinal, "value": _coded(code)})


def _specimen_rows(engine: Engine) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT e.business_key, pv.ordinal, pv.value FROM property_value pv "
                "JOIN catalogue_entry e ON e.id = pv.entry_id "
                "WHERE pv.property_key = 'specimen' ORDER BY 1, 2"
            )
        ).all()
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for key, ordinal, value in rows:
        grouped.setdefault(key, []).append((ordinal, value))
    return grouped


def _column_exists(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name = 'catalogue_entry' AND column_name = 'specimen_unconstrained'"
                )
            ).scalar_one()
        )


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_a_flagged_entry_becomes_the_specimen_root_and_the_column_goes(engine: Engine) -> None:
    _at_0023_with(engine, {"NPTC-900001": True})

    _migrate(engine, "upgrade", "0024")

    assert _specimen_rows(engine) == {
        "NPTC-900001": [(0, {"system": "http://snomed.info/sct", "code": _ROOT, "display": "Any"})]
    }
    assert not _column_exists(engine)


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_an_unflagged_entry_gets_no_specimen_value(engine: Engine) -> None:
    _at_0023_with(engine, {"NPTC-900001": False})

    _migrate(engine, "upgrade", "0024")

    assert _specimen_rows(engine) == {}


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_a_flagged_entry_that_already_holds_a_specimen_keeps_only_its_named_values(
    engine: Engine,
) -> None:
    _at_0023_with(engine, {"NPTC-900001": True}, {"NPTC-900001": ["119364003"]})

    _migrate(engine, "upgrade", "0024")

    assert [row[1]["code"] for row in _specimen_rows(engine)["NPTC-900001"]] == ["119364003"]


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_upgrade_names_each_flagged_entry_whose_flag_it_drops_unconverted(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    _at_0023_with(
        engine,
        {"NPTC-900001": True, "NPTC-900002": True, "NPTC-900003": False},
        {"NPTC-900001": ["119364003"], "NPTC-900003": ["119364003"]},
    )

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        _migrate(engine, "upgrade", "0024")

    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "1 " in record.getMessage()
    assert "NPTC-900001" in record.getMessage()
    assert "NPTC-900002" not in record.getMessage()
    assert "NPTC-900003" not in record.getMessage()


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_upgrade_is_silent_when_every_flag_converts_cleanly(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    _at_0023_with(engine, {"NPTC-900001": True, "NPTC-900002": False})

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        _migrate(engine, "upgrade", "0024")

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_conversion_leaves_row_version_alone(engine: Engine) -> None:
    _at_0023_with(engine, {"NPTC-900001": True})
    with engine.connect() as connection:
        before = connection.execute(text("SELECT row_version FROM catalogue_entry")).scalar_one()

    _migrate(engine, "upgrade", "0024")

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT row_version FROM catalogue_entry")).scalar_one()
            == before
        )


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_upgrade_refuses_when_flagged_entries_have_no_specimen_definition(
    engine: Engine,
) -> None:
    _at_0023_with(engine, {"NPTC-900001": True}, definition=False)

    with pytest.raises(RuntimeError, match="'specimen' property definition does not exist"):
        _migrate(engine, "upgrade", "0024")

    assert _column_exists(engine)  # nothing was dropped


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_a_catalogue_with_no_flagged_entry_upgrades_with_no_definition(engine: Engine) -> None:
    _at_0023_with(engine, {"NPTC-900001": False}, definition=False)

    _migrate(engine, "upgrade", "0024")

    assert not _column_exists(engine)


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_downgrade_flags_an_entry_whose_only_specimen_is_the_root(engine: Engine) -> None:
    _at_0023_with(
        engine,
        {"NPTC-900001": False, "NPTC-900002": False, "NPTC-900003": False},
        {
            "NPTC-900001": [_ROOT],
            "NPTC-900002": ["119364003"],
            "NPTC-900003": [_ROOT, "119364003"],
        },
    )
    _migrate(engine, "upgrade", "0024")

    _migrate(engine, "downgrade", "0023")

    with engine.connect() as connection:
        flags = dict(
            connection.execute(
                text("SELECT business_key, specimen_unconstrained FROM catalogue_entry")
            ).all()
        )
    assert flags == {"NPTC-900001": True, "NPTC-900002": False, "NPTC-900003": False}
    remaining = _specimen_rows(engine)
    assert "NPTC-900001" not in remaining  # the old rule: the flag or specimens, never both
    assert [row[1]["code"] for row in remaining["NPTC-900002"]] == ["119364003"]
    assert [row[1]["code"] for row in remaining["NPTC-900003"]] == [_ROOT, "119364003"]


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_downgrade_gives_the_app_role_its_column_grant_back(engine: Engine) -> None:
    _at_0023_with(engine, {"NPTC-900001": False})
    _migrate(engine, "upgrade", "0024")

    _migrate(engine, "downgrade", "0023")

    with engine.connect() as connection:
        granted = connection.execute(
            text(
                "SELECT has_column_privilege('nptc_app', 'catalogue_entry', "
                "'specimen_unconstrained', 'UPDATE')"
            )
        ).scalar_one()
    assert granted


def _plant_trigger(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION public.planted_function() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NEW; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER planted_trigger BEFORE INSERT ON property_value "
                "FOR EACH ROW EXECUTE FUNCTION public.planted_function()"
            )
        )


def _remove_trigger(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER IF EXISTS planted_trigger ON property_value"))
        connection.execute(text("DROP FUNCTION IF EXISTS public.planted_function()"))


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_the_upgrade_refuses_to_write_property_value_while_a_trigger_is_on_it(
    engine: Engine,
) -> None:
    """Migration 0025 lets another login create triggers on `property_value`, and the conversion
    would fire one as the migration role."""
    _at_0023_with(engine, {"NPTC-900001": True})
    _plant_trigger(engine)
    try:
        with pytest.raises(ForeignCodeOnPropertyValueError):
            _migrate(engine, "upgrade", "0024")

        assert _revision(engine) == "0023"
        assert _specimen_rows(engine) == {}
    finally:
        _remove_trigger(engine)


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_the_downgrade_refuses_to_write_property_value_while_a_trigger_is_on_it(
    engine: Engine,
) -> None:
    _at_0023_with(engine, {"NPTC-900001": False}, {"NPTC-900001": [_ROOT]})
    _migrate(engine, "upgrade", "0024")
    _plant_trigger(engine)
    try:
        with pytest.raises(ForeignCodeOnPropertyValueError):
            _migrate(engine, "downgrade", "0023")

        assert _revision(engine) == "0024"
    finally:
        _remove_trigger(engine)
