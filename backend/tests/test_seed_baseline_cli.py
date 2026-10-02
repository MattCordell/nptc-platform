"""scripts/seed_baseline.py against a real Postgres (FR-76): the commit boundary, the exit codes
and what reaches the operator's terminal. The CLI commits for real here, so each test requests
`pristine_catalogue` and `pristine_audit_event` to clean up after itself.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

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
    document["entries"][1]["properties"]["specimen"] = [{"value": "Amniotic fluid", "code": None}]

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
