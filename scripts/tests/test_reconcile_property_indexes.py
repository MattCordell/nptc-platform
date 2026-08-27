"""Offline unit tests for scripts/reconcile_property_indexes.py (issue #54):
argument parsing, DSN resolution, and the mapping from a `ReconciliationReport`
to exit code and rendered output. No Docker/Postgres here - the real
reconciler is exercised by backend/tests/test_db_property_indexes.py
instead; this module fabricates `ReconciliationReport` values to drive
`main()`'s own logic in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reconcile_property_indexes as cli

from nptc.db.property_reconciler import ReconciliationReport

# --- DSN resolution ---------------------------------------------------------


def test_cli_flag_takes_precedence_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")
    assert cli._resolve_database_url("postgresql://cli") == "postgresql://cli"


def test_falls_back_to_indexer_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")
    assert cli._resolve_database_url(None) == "postgresql://env"


def test_empty_cli_flag_is_rejected_rather_than_falling_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", "postgresql://env")
    with pytest.raises(ValueError, match="--database-url must not be empty"):
        cli._resolve_database_url("")


def test_no_dsn_configured_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_INDEXER_DATABASE_URL", raising=False)
    assert cli.main([]) == cli.EXIT_USAGE_ERROR


# --- report -> exit code / output -------------------------------------------


def _patch_reconcile(monkeypatch: pytest.MonkeyPatch, report: ReconciliationReport) -> MagicMock:
    mock = MagicMock(return_value=report)
    fake_module = MagicMock()
    fake_module.reconcile_property_indexes = mock
    fake_module.get_indexer_engine.cache_clear = MagicMock()
    monkeypatch.setattr("nptc.db.property_reconciler", fake_module)
    return mock


def test_no_drift_is_exit_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_reconcile(monkeypatch, ReconciliationReport())

    code = cli.main(["--database-url", "postgresql://x"])

    assert code == cli.EXIT_OK
    assert "OK: no drift" in capsys.readouterr().out


def test_real_run_that_converges_is_exit_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real (non-dry-run) reconciliation that finds and fixes drift is
    still success - fixing it is the whole point of running it."""
    _patch_reconcile(monkeypatch, ReconciliationReport(created=("ix_propval_p1_1",)))

    code = cli.main(["--database-url", "postgresql://x"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "CREATED: ix_propval_p1_1" in out


def test_dry_run_that_finds_drift_is_exit_drift_remains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mock = _patch_reconcile(monkeypatch, ReconciliationReport(created=("ix_propval_p1_1",)))

    code = cli.main(["--database-url", "postgresql://x", "--dry-run"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_DRIFT_REMAINS
    assert "WOULD CREATE: ix_propval_p1_1" in out
    mock.assert_called_once_with(dry_run=True)


def test_dry_run_with_no_drift_is_exit_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_reconcile(monkeypatch, ReconciliationReport())

    code = cli.main(["--database-url", "postgresql://x", "--dry-run"])

    assert code == cli.EXIT_OK
    assert "OK: no drift" in capsys.readouterr().out


def test_dropped_and_repaired_are_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_reconcile(
        monkeypatch,
        ReconciliationReport(
            dropped=("ix_propval_p2_1",),
            repaired_invalid=("ix_propval_p3_1",),
            repaired_comment=("ix_propval_p4_1",),
        ),
    )

    code = cli.main(["--database-url", "postgresql://x"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "DROPPED: ix_propval_p2_1" in out
    assert "REBUILT (was invalid): ix_propval_p3_1" in out
    assert "REPAIRED COMMENT: ix_propval_p4_1" in out


def test_skipped_locked_is_exit_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_reconcile(monkeypatch, ReconciliationReport(skipped_locked=True))

    code = cli.main(["--database-url", "postgresql://x"])

    assert code == cli.EXIT_OK
    assert "SKIPPED" in capsys.readouterr().out


def test_reconciliation_failure_is_could_not_complete_never_the_raw_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NFR-26: the exception's type name is reported, never its message -
    a connection failure can carry host/user/dbname details that don't
    belong in operator-facing output."""
    fake_module = MagicMock()
    fake_module.reconcile_property_indexes.side_effect = RuntimeError(
        "connection to host secret-db-host.internal failed"
    )
    monkeypatch.setattr("nptc.db.property_reconciler", fake_module)

    code = cli.main(["--database-url", "postgresql://x"])

    err = capsys.readouterr().err
    assert code == cli.EXIT_COULD_NOT_COMPLETE
    assert "RuntimeError" in err
    assert "secret-db-host" not in err
