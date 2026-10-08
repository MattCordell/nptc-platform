"""scripts/seed_baseline.py against a real Postgres (FR-76): the commit boundary, the exit codes
and what reaches the operator's terminal. The CLI commits for real here, so each test requests
`pristine_catalogue` and `pristine_audit_event` to clean up after itself.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from testcontainers.community.postgres import PostgresContainer

from nptc.db import property_reconciler
from nptc.db.property_indexes import index_name
from nptc.db.property_reconciler import ReconciliationReport

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("pristine_catalogue", "pristine_audit_event"),
]

MakeDocument = Callable[..., dict[str, Any]]
WriteDataset = Callable[[object], Path]

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_baseline.py"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_baseline_cli_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _committed_count(owner_engine: Engine, table: str) -> int:
    # `table` is one of this module's own literals, never runtime data.
    statements = {
        "catalogue_entry": "SELECT count(*) FROM catalogue_entry",
        "seed_import": "SELECT count(*) FROM seed_import",
        "entry_seed_provenance": "SELECT count(*) FROM entry_seed_provenance",
    }
    with owner_engine.connect() as connection:
        return int(connection.execute(text(statements[table])).scalar_one())


def _run(app_engine: Engine, dataset: Path, *extra: str) -> int:
    url = app_engine.url.render_as_string(hide_password=False)
    return int(cli.main(["--dataset", str(dataset), "--database-url", url, *extra]))


@pytest.mark.req("FR-76")
def test_a_successful_run_commits_the_baseline(
    app_engine: Engine,
    owner_engine: Engine,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(app_engine, write_dataset(make_dataset_document(2)))

    assert code == cli.EXIT_OK
    assert "SEEDED 2 entries" in capsys.readouterr().out
    assert _committed_count(owner_engine, "catalogue_entry") == 2
    assert _committed_count(owner_engine, "seed_import") == 1
    assert _committed_count(owner_engine, "entry_seed_provenance") == 2


@pytest.mark.req("FR-76")
def test_running_again_refuses_with_its_own_exit_code_and_changes_nothing(
    app_engine: Engine,
    owner_engine: Engine,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_dataset(make_dataset_document(2))
    assert _run(app_engine, dataset) == cli.EXIT_OK
    capsys.readouterr()

    code = _run(app_engine, dataset)

    assert code == cli.EXIT_CATALOGUE_NOT_EMPTY
    assert "nothing was written" in capsys.readouterr().err
    assert _committed_count(owner_engine, "catalogue_entry") == 2
    assert _committed_count(owner_engine, "seed_import") == 1


@pytest.mark.req("FR-76")
def test_a_dry_run_reports_success_and_commits_nothing(
    app_engine: Engine,
    owner_engine: Engine,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(app_engine, write_dataset(make_dataset_document(2)), "--dry-run")

    assert code == cli.EXIT_OK
    assert "DRY RUN" in capsys.readouterr().out
    assert _committed_count(owner_engine, "catalogue_entry") == 0
    assert _committed_count(owner_engine, "seed_import") == 0


@pytest.mark.req("FR-05")
def test_a_collision_rolls_the_whole_import_back(
    app_engine: Engine,
    owner_engine: Engine,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = make_dataset_document(3)
    clash = document["entries"][0]["preferred_term"]
    document["entries"][2]["preferred_term"] = clash
    document["entries"][2]["designations"][0]["term"] = clash

    code = _run(app_engine, write_dataset(document))

    assert code == cli.EXIT_IMPORT_REFUSED
    err = capsys.readouterr().err
    assert "NPTC-500002" in err
    assert "rolled back" in err
    assert _committed_count(owner_engine, "catalogue_entry") == 0
    assert _committed_count(owner_engine, "seed_import") == 0


@pytest.mark.req("FR-76")
def test_an_uncoded_specimen_refuses_the_dataset_and_names_the_value(
    app_engine: Engine,
    owner_engine: Engine,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = make_dataset_document(2)
    document["entries"][1]["properties"]["specimen"] = [
        {"value": "Amniotic fluid", "code": None, "display": None}
    ]

    code = _run(app_engine, write_dataset(document))

    assert code == cli.EXIT_DATASET_REFUSED
    err = capsys.readouterr().err
    assert "Amniotic fluid" in err
    assert "NPTC-500001" in err
    assert _committed_count(owner_engine, "catalogue_entry") == 0


@pytest.mark.req("NFR-26")
def test_an_unreachable_database_never_prints_the_credentials(
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_dataset(make_dataset_document(1))
    dsn = "postgresql+psycopg://nobody:hunter2-not-real@127.0.0.1:1/none?connect_timeout=2"

    code = int(cli.main(["--dataset", str(dataset), "--database-url", dsn]))

    assert code == cli.EXIT_COULD_NOT_COMPLETE
    captured = capsys.readouterr()
    assert "hunter2-not-real" not in captured.err + captured.out
    assert "OperationalError" in captured.err


def test_the_dataset_file_is_left_untouched(
    app_engine: Engine, make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(1)
    path = write_dataset(document)

    _run(app_engine, path, "--dry-run")

    assert json.loads(path.read_text(encoding="utf-8")) == document


_FILTERABLE_SYSTEM_KEYS = ["discipline", "subgroup", "specimen"]
_UNREACHABLE_INDEXER_DSN = (
    "postgresql+psycopg://nobody:hunter2-not-real@127.0.0.1:1/none?connect_timeout=2"
)


@pytest.fixture(autouse=True)
def _no_ambient_indexer_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `NPTC_INDEXER_DATABASE_URL` must not make a test build indexes in a
    database it never named."""
    monkeypatch.delenv("NPTC_INDEXER_DATABASE_URL", raising=False)


def _system_index_names(owner_engine: Engine, keys: list[str]) -> list[str]:
    """The generated index name for each of `keys` that has a definition row. The name derives
    from the row's `index_seq`, so it is read, never assumed."""
    with owner_engine.connect() as connection:
        sequences = connection.execute(
            text("SELECT index_seq FROM property_definition WHERE key = ANY(:keys)"),
            {"keys": keys},
        ).scalars()
        return [index_name(sequence, 1) for sequence in sequences]


def _valid_indexes(owner_engine: Engine, names: list[str]) -> set[str]:
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = ANY(:names) AND i.indisvalid"
            ),
            {"names": names},
        ).scalars()
        return set(rows)


def _drop_system_indexes(owner_engine: Engine) -> None:
    names = _system_index_names(owner_engine, [*_FILTERABLE_SYSTEM_KEYS, "usage_guidance"])
    with owner_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for name in names:
            connection.execute(text(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"'))


@pytest.fixture
def no_system_property_indexes(owner_engine: Engine, migrated: None) -> Iterator[None]:
    """The system properties' definition rows outlive every test in the shared container, and so
    can their indexes, if another module ran the reconciler. Drops them before and after, so a
    test sees only what its own run built."""
    _drop_system_indexes(owner_engine)
    yield
    _drop_system_indexes(owner_engine)


@pytest.mark.req("FR-13")
def test_a_run_builds_the_indexes_of_the_filterable_system_properties(
    app_engine: Engine,
    owner_engine: Engine,
    postgres_container: PostgresContainer,
    no_system_property_indexes: None,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(
        app_engine,
        write_dataset(make_dataset_document(2)),
        "--indexer-database-url",
        postgres_container.get_connection_url(),
    )

    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    names = _system_index_names(owner_engine, _FILTERABLE_SYSTEM_KEYS)
    assert len(names) == len(_FILTERABLE_SYSTEM_KEYS)
    assert _valid_indexes(owner_engine, names) == set(names)
    for name in names:
        assert f"created index: {name}" in captured.out
    assert "WARNING" not in captured.err
    unfiltered = _system_index_names(owner_engine, ["usage_guidance"])
    assert _valid_indexes(owner_engine, unfiltered) == set()


@pytest.mark.req("FR-13")
def test_without_an_indexer_dsn_the_baseline_commits_and_says_no_index_was_built(
    app_engine: Engine,
    owner_engine: Engine,
    no_system_property_indexes: None,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(app_engine, write_dataset(make_dataset_document(2)))

    assert code == cli.EXIT_OK
    assert _committed_count(owner_engine, "catalogue_entry") == 2
    err = capsys.readouterr().err
    assert "no property index was built" in err
    assert "scripts/reconcile_property_indexes.py" in err
    names = _system_index_names(owner_engine, _FILTERABLE_SYSTEM_KEYS)
    assert _valid_indexes(owner_engine, names) == set()


@pytest.mark.req("FR-13")
@pytest.mark.req("NFR-26")
def test_an_unreachable_indexer_leaves_the_baseline_committed_and_prints_no_credentials(
    app_engine: Engine,
    owner_engine: Engine,
    no_system_property_indexes: None,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(
        app_engine,
        write_dataset(make_dataset_document(2)),
        "--indexer-database-url",
        _UNREACHABLE_INDEXER_DSN,
    )

    assert code == cli.EXIT_OK
    assert _committed_count(owner_engine, "catalogue_entry") == 2
    assert _committed_count(owner_engine, "seed_import") == 1
    captured = capsys.readouterr()
    assert "OperationalError" in captured.err
    assert "scripts/reconcile_property_indexes.py" in captured.err
    assert "hunter2-not-real" not in captured.err + captured.out
    assert "SEEDED 2 entries" in captured.out


@pytest.mark.req("FR-13")
def test_a_dry_run_never_starts_the_reconciler(
    app_engine: Engine,
    owner_engine: Engine,
    postgres_container: PostgresContainer,
    no_system_property_indexes: None,
    make_dataset_document: MakeDocument,
    write_dataset: WriteDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def record(**kwargs: object) -> ReconciliationReport:
        calls.append(kwargs)
        return ReconciliationReport()

    monkeypatch.setattr(property_reconciler, "reconcile_property_indexes", record)

    code = _run(
        app_engine,
        write_dataset(make_dataset_document(2)),
        "--dry-run",
        "--indexer-database-url",
        postgres_container.get_connection_url(),
    )

    assert code == cli.EXIT_OK
    assert calls == []
    names = _system_index_names(owner_engine, _FILTERABLE_SYSTEM_KEYS)
    assert _valid_indexes(owner_engine, names) == set()
