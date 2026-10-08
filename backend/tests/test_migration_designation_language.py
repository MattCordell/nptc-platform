"""Migration 0026: `designation.language`, `designation.use` and
`designation_collision_acknowledgement.language` are dropped (FR-04, FR-05, ADR-0022).

Runs on its own database, because it moves the schema back and forward.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB = "nptc_designation_language"

_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term, preferred_term_key) "
    "VALUES (:key, :key, :key)"
)
_DESIGNATION = text(
    "INSERT INTO designation (entry_id, term, term_key, use, language, status, retired_at) "
    "SELECT id, :term, :term_key, :use, :language, :status, "
    "CASE WHEN :status = 'retired' THEN now() END "
    "FROM catalogue_entry WHERE business_key = :key"
)
_DESIGNATION_AT_HEAD = text(
    "INSERT INTO designation (entry_id, term, term_key, status, retired_at) "
    "SELECT id, :term, :term_key, :status, "
    "CASE WHEN :status = 'retired' THEN now() END "
    "FROM catalogue_entry WHERE business_key = :key"
)
_ACKNOWLEDGEMENT = text(
    "INSERT INTO designation_collision_acknowledgement (entry_id, term_key, language, reason) "
    "SELECT id, :term_key, :language, 'reviewed' FROM catalogue_entry WHERE business_key = :key"
)


def _config() -> Config:
    return Config(toml_file=str(REPO_ROOT / "pyproject.toml"))


def _migrate(engine: Engine, direction: str, revision: str) -> None:
    config = _config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)
        connection.commit()


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _at(engine: Engine, revision: str) -> None:
    """Brings the database to exactly `revision`, whichever side of it it is on. Revisions are
    zero-padded, so a string comparison orders them."""
    current = _revision(engine)
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


def _at_0025_with(
    engine: Engine,
    designations: list[dict[str, Any]],
    acknowledgements: list[dict[str, Any]] | None = None,
) -> None:
    """The schema at 0025 holding `designations` and `acknowledgements`. An entry is created for
    each distinct `key` named."""
    _at(engine, "0025")
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM designation_collision_acknowledgement"))
        connection.execute(text("DELETE FROM designation"))
        connection.execute(text("DELETE FROM catalogue_entry"))
        keys = {row["key"] for row in designations} | {row["key"] for row in acknowledgements or []}
        for key in sorted(keys):
            connection.execute(_ENTRY, {"key": key})
        for row in designations:
            connection.execute(
                _DESIGNATION,
                {"use": "synonym", "language": "en-AU", "status": "active", **row},
            )
        for row in acknowledgements or []:
            connection.execute(_ACKNOWLEDGEMENT, {"language": "en-AU", **row})


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            ).scalars()
        )


def _constraints(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
                {"t": table},
            ).scalars()
        )


def _index_definitions(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
                {"t": table},
            ).all()
        )


_SYNONYM = {"key": "NPTC-900001", "term": "Full blood count", "term_key": "full blood count"}


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_the_upgrade_drops_the_language_and_use_columns_and_their_constraints(
    engine: Engine,
) -> None:
    _at_0025_with(engine, [_SYNONYM], [{"key": "NPTC-900001", "term_key": "fbc"}])

    _migrate(engine, "upgrade", "0026")

    assert not {"language", "use"} & _columns(engine, "designation")
    assert "language" not in _columns(engine, "designation_collision_acknowledgement")
    constraints = _constraints(engine, "designation")
    assert (
        not {
            "ck_designation_use",
            "ck_designation_language",
            "ck_designation_no_en_au_preferred",
        }
        & constraints
    )
    assert "ck_designation_status" in constraints
    assert "ck_designation_collision_acknowledgement_language" not in _constraints(
        engine, "designation_collision_acknowledgement"
    )
    assert "ix_designation_one_active_preferred_per_entry_language" not in _index_definitions(
        engine, "designation"
    )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_the_upgrade_keeps_every_row(engine: Engine) -> None:
    _at_0025_with(
        engine,
        [_SYNONYM, {**_SYNONYM, "term": "FBC", "term_key": "fbc", "status": "retired"}],
        [{"key": "NPTC-900001", "term_key": "fbc"}],
    )

    _migrate(engine, "upgrade", "0026")

    with engine.connect() as connection:
        kept = connection.execute(text("SELECT term, status FROM designation ORDER BY term")).all()
        acknowledged = (
            connection.execute(text("SELECT term_key FROM designation_collision_acknowledgement"))
            .scalars()
            .all()
        )
    assert kept == [("FBC", "retired"), ("Full blood count", "active")]
    assert acknowledged == ["fbc"]


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_the_same_active_synonym_cannot_be_added_twice_to_one_entry(engine: Engine) -> None:
    """The principal failure mode: the rebuilt index still blocks a duplicate."""
    _at_0025_with(engine, [_SYNONYM])
    _migrate(engine, "upgrade", "0026")

    with (
        pytest.raises(IntegrityError, match="ix_designation_no_duplicate_active_term"),
        engine.begin() as connection,
    ):
        connection.execute(_DESIGNATION_AT_HEAD, {**_SYNONYM, "status": "active"})


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_case_or_punctuation_variant_shares_the_term_key_and_is_blocked_too(
    engine: Engine,
) -> None:
    _at_0025_with(engine, [_SYNONYM])
    _migrate(engine, "upgrade", "0026")

    with (
        pytest.raises(IntegrityError, match="ix_designation_no_duplicate_active_term"),
        engine.begin() as connection,
    ):
        connection.execute(
            _DESIGNATION_AT_HEAD,
            {**_SYNONYM, "term": "FULL  Blood-Count", "status": "active"},
        )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_a_retired_duplicate_and_another_entrys_synonym_are_still_allowed(
    engine: Engine,
) -> None:
    _at_0025_with(engine, [_SYNONYM, {**_SYNONYM, "key": "NPTC-900002"}])
    _migrate(engine, "upgrade", "0026")

    with engine.begin() as connection:
        connection.execute(_DESIGNATION_AT_HEAD, {**_SYNONYM, "status": "retired"})

    with engine.connect() as connection:
        total = connection.execute(text("SELECT count(*) FROM designation")).scalar_one()
    assert total == 3


@pytest.mark.req("FR-04")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("row", "named"),
    [
        ({"language": "mi-NZ"}, "language 'mi-NZ'"),
        ({"language": "mi-NZ", "use": "preferred"}, "use 'preferred'"),
    ],
)
def test_the_upgrade_refuses_a_designation_that_is_not_an_en_au_synonym(
    engine: Engine, row: dict[str, str], named: str
) -> None:
    _at_0025_with(engine, [_SYNONYM, {**_SYNONYM, "term": "Hemogram", **row}])

    with pytest.raises(RuntimeError, match="not an en-AU synonym") as raised:
        _migrate(engine, "upgrade", "0026")

    assert "NPTC-900001" in str(raised.value)
    assert "Hemogram" in str(raised.value)
    assert named in str(raised.value)
    assert "Full blood count" not in str(raised.value)
    assert _revision(engine) == "0025"
    assert "language" in _columns(engine, "designation")


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_the_upgrade_refuses_an_acknowledgement_in_another_language(engine: Engine) -> None:
    _at_0025_with(engine, [_SYNONYM], [{"key": "NPTC-900001", "term_key": "fbc", "language": "fr"}])

    with pytest.raises(RuntimeError, match="collision acknowledgement on NPTC-900001"):
        _migrate(engine, "upgrade", "0026")

    assert _revision(engine) == "0025"
    assert "language" in _columns(engine, "designation_collision_acknowledgement")


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_the_refusal_lists_at_most_fifty_rows_and_counts_the_rest(engine: Engine) -> None:
    _at_0025_with(
        engine,
        [{**_SYNONYM, "term": f"T{n}", "term_key": f"t{n}", "language": "fr"} for n in range(52)],
    )

    with pytest.raises(RuntimeError, match="52 rows") as raised:
        _migrate(engine, "upgrade", "0026")

    assert "and 2 more" in str(raised.value)
    assert str(raised.value).count("designation on NPTC-900001") == 50


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_the_downgrade_restores_the_columns_constraints_indexes_and_grant(
    engine: Engine,
) -> None:
    _at_0025_with(engine, [_SYNONYM], [{"key": "NPTC-900001", "term_key": "fbc"}])
    _migrate(engine, "upgrade", "0026")

    _migrate(engine, "downgrade", "0025")

    with engine.connect() as connection:
        restored = connection.execute(text("SELECT use, language FROM designation")).one()
        acknowledged = connection.execute(
            text("SELECT language FROM designation_collision_acknowledgement")
        ).scalar_one()
        granted = connection.execute(
            text(
                "SELECT has_column_privilege('nptc_app', 'designation', 'use', 'UPDATE') "
                "AND has_column_privilege('nptc_app', 'designation', 'language', 'UPDATE')"
            )
        ).scalar_one()
    assert restored == ("synonym", "en-AU")
    assert acknowledged == "en-AU"
    assert granted
    assert {
        "ck_designation_use",
        "ck_designation_language",
        "ck_designation_no_en_au_preferred",
    } <= _constraints(engine, "designation")
    assert "ck_designation_collision_acknowledgement_language" in _constraints(
        engine, "designation_collision_acknowledgement"
    )
    indexes = _index_definitions(engine, "designation")
    assert "(entry_id, term_key, language)" in indexes["ix_designation_no_duplicate_active_term"]
    assert "ix_designation_one_active_preferred_per_entry_language" in indexes
    assert (
        "(entry_id, term_key, language)"
        in _index_definitions(engine, "designation_collision_acknowledgement")[
            "ix_designation_collision_ack_entry_term_language"
        ]
    )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_after_the_downgrade_the_old_rule_on_an_en_au_preferred_row_holds_again(
    engine: Engine,
) -> None:
    _at_0025_with(engine, [_SYNONYM])
    _migrate(engine, "upgrade", "0026")
    _migrate(engine, "downgrade", "0025")

    with (
        pytest.raises(IntegrityError, match="ck_designation_no_en_au_preferred"),
        engine.begin() as connection,
    ):
        connection.execute(
            _DESIGNATION,
            {
                **_SYNONYM,
                "term": "Other",
                "term_key": "other",
                "use": "preferred",
                "language": "en-AU",
                "status": "active",
            },
        )
