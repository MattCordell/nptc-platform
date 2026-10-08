"""Offline unit tests for scripts/seed_baseline.py (FR-76): argument parsing, DSN resolution and
the refusals that happen before any connection. No Docker or Postgres here. The commit, rollback
and exit-code behaviour against a real database is exercised by
backend/tests/test_seed_baseline_cli.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nptc.db import property_reconciler
from nptc.db.property_reconciler import ReconciliationReport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seed_baseline as cli

_UNREACHABLE_DSN = "postgresql+psycopg://nobody:never-printed@127.0.0.1:1/none"
_INDEXER_DSN = "postgresql+psycopg://nptc_indexer:hunter2@127.0.0.1:1/none"


def test_dataset_is_required() -> None:
    with pytest.raises(SystemExit):
        cli._parse_args([])


def test_parse_args_defaults() -> None:
    args = cli._parse_args(["--dataset", "import-dataset.json"])

    assert args.dataset == Path("import-dataset.json")
    assert args.database_url is None
    assert args.indexer_database_url is None
    assert args.dry_run is False


def test_parse_args_accepts_dry_run() -> None:
    assert cli._parse_args(["--dataset", "x.json", "--dry-run"]).dry_run is True


def test_cli_flag_takes_precedence_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://env")
    assert cli._resolve_database_url("postgresql://cli") == "postgresql://cli"


def test_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://env")
    assert cli._resolve_database_url(None) == "postgresql://env"


def test_an_empty_flag_is_rejected_rather_than_falling_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://env")
    with pytest.raises(ValueError, match="--database-url must not be empty"):
        cli._resolve_database_url("")


def test_no_dsn_configured_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    assert cli.main(["--dataset", "x.json"]) == cli.EXIT_USAGE_ERROR


def test_exit_codes_are_distinct() -> None:
    codes = [
        cli.EXIT_OK,
        cli.EXIT_USAGE_ERROR,
        cli.EXIT_DATASET_REFUSED,
        cli.EXIT_CATALOGUE_NOT_EMPTY,
        cli.EXIT_IMPORT_REFUSED,
        cli.EXIT_COULD_NOT_COMPLETE,
    ]
    assert len(set(codes)) == len(codes)


def test_a_missing_dataset_is_refused_before_any_connection(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = cli.main(
        ["--dataset", str(tmp_path / "absent.json"), "--database-url", _UNREACHABLE_DSN]
    )

    assert code == cli.EXIT_DATASET_REFUSED
    assert "nothing was written" in capsys.readouterr().err


def test_an_unsupported_schema_version_is_refused_before_any_connection(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "import-dataset.json"
    path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")

    code = cli.main(["--dataset", str(path), "--database-url", _UNREACHABLE_DSN])

    assert code == cli.EXIT_DATASET_REFUSED
    assert "schema_version" in capsys.readouterr().err


@pytest.mark.req("FR-13")
def test_indexer_flag_takes_precedence_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")
    assert cli._resolve_indexer_database_url("postgresql://cli") == "postgresql://cli"


@pytest.mark.req("FR-13")
def test_indexer_url_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")
    assert cli._resolve_indexer_database_url(None) == "postgresql://env"


@pytest.mark.req("FR-13")
def test_an_unset_indexer_url_resolves_to_none_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NPTC_INDEXER_DATABASE_URL", raising=False)
    assert cli._resolve_indexer_database_url(None) is None


@pytest.mark.req("FR-13")
def test_an_empty_indexer_flag_is_a_usage_error_before_any_work(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")

    code = cli.main(
        [
            "--dataset",
            "x.json",
            "--database-url",
            _UNREACHABLE_DSN,
            "--indexer-database-url",
            "",
        ]
    )

    assert code == cli.EXIT_USAGE_ERROR
    assert "--indexer-database-url must not be empty" in capsys.readouterr().err


def _reconcile_returns(
    monkeypatch: pytest.MonkeyPatch, report: ReconciliationReport
) -> list[str | None]:
    """Replaces the reconciler with one that returns `report` and records the DSN it was given."""
    seen: list[str | None] = []

    def fake(*, dry_run: bool = False, database_url: str | None = None) -> ReconciliationReport:
        seen.append(database_url)
        return report

    monkeypatch.setattr(property_reconciler, "reconcile_property_indexes", fake)
    return seen


@pytest.mark.req("FR-13")
def test_building_indexes_with_no_dsn_warns_and_never_calls_the_reconciler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _reconcile_returns(monkeypatch, ReconciliationReport())

    cli._build_indexes(None)

    assert seen == []
    err = capsys.readouterr().err
    assert "no property index was built" in err
    assert "scripts/reconcile_property_indexes.py" in err


@pytest.mark.req("FR-13")
def test_created_indexes_are_listed_and_the_dsn_is_passed_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _reconcile_returns(
        monkeypatch, ReconciliationReport(created=("ix_propval_p1_1", "ix_propval_p2_1"))
    )

    cli._build_indexes(_INDEXER_DSN)

    captured = capsys.readouterr()
    assert seen == [_INDEXER_DSN]
    assert "created index: ix_propval_p1_1" in captured.out
    assert "created index: ix_propval_p2_1" in captured.out
    assert captured.err == ""


@pytest.mark.req("FR-13")
def test_a_reconciler_that_raises_is_a_warning_naming_only_the_exception_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(**_: object) -> ReconciliationReport:
        raise ConnectionError(f"could not connect to {_INDEXER_DSN}")

    monkeypatch.setattr(property_reconciler, "reconcile_property_indexes", boom)

    cli._build_indexes(_INDEXER_DSN)

    err = capsys.readouterr().err
    assert "ConnectionError" in err
    assert "hunter2" not in err
    assert "127.0.0.1" not in err
    assert "scripts/reconcile_property_indexes.py" in err


@pytest.mark.req("FR-13")
def test_a_failed_index_is_a_warning_naming_the_index_and_the_exception_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _reconcile_returns(
        monkeypatch, ReconciliationReport(failed=(("ix_propval_p3_1", "InsufficientPrivilege"),))
    )

    cli._build_indexes(_INDEXER_DSN)

    err = capsys.readouterr().err
    assert "ix_propval_p3_1" in err
    assert "InsufficientPrivilege" in err


@pytest.mark.req("FR-13")
def test_a_held_reconciliation_lock_is_a_warning_not_a_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _reconcile_returns(monkeypatch, ReconciliationReport(skipped_locked=True))

    cli._build_indexes(_INDEXER_DSN)

    assert len(seen) == 1
    assert "another reconciliation is in progress" in capsys.readouterr().err


@pytest.mark.req("FR-13")
def test_a_property_with_no_datatype_handler_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _reconcile_returns(monkeypatch, ReconciliationReport(skipped_unknown_datatype=("mystery",)))

    cli._build_indexes(_INDEXER_DSN)

    assert "'mystery'" in capsys.readouterr().err
