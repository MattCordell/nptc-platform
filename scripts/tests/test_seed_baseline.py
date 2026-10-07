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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seed_baseline as cli

_UNREACHABLE_DSN = "postgresql+psycopg://nobody:never-printed@127.0.0.1:1/none"


def test_dataset_is_required() -> None:
    with pytest.raises(SystemExit):
        cli._parse_args([])


def test_parse_args_defaults() -> None:
    args = cli._parse_args(["--dataset", "import-dataset.json"])

    assert args.dataset == Path("import-dataset.json")
    assert args.database_url is None
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
