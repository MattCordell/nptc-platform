"""Offline unit tests for scripts/verify_audit_chain.py (issue #38): argument
parsing, DSN resolution precedence, and the mapping from a `ChainVerification`
to exit code and rendered message. No Docker/Postgres here - the real
`verify_chain` walk is exercised by
backend/tests/test_verify_audit_chain_cli.py instead; this module fabricates
`ChainVerification` values to drive `main()`'s own logic in isolation.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_audit_chain as verify

from nptc.audit.verification import ChainVerification

_OK_HASH = "a" * 64
_OTHER_HASH = "b" * 64


def _ok(record_count: int = 3, head_hash: str | None = _OK_HASH) -> ChainVerification:
    return ChainVerification(
        ok=True,
        record_count=record_count,
        first_sequence=1 if record_count else None,
        last_sequence=record_count if record_count else None,
        first_broken_sequence=None,
        break_reason=None,
        head_hash=head_hash,
    )


def _broken() -> ChainVerification:
    return ChainVerification(
        ok=False,
        record_count=2,
        first_sequence=1,
        last_sequence=2,
        first_broken_sequence=2,
        break_reason="entry_hash mismatch",
        head_hash=_OK_HASH,
    )


@contextmanager
def _patched_verify_chain(
    monkeypatch: pytest.MonkeyPatch, result: ChainVerification
) -> Iterator[MagicMock]:
    mock = MagicMock(return_value=result)
    monkeypatch.setattr("nptc.audit.verification.verify_chain", mock)
    fake_connection = MagicMock()
    fake_engine = MagicMock()
    fake_engine.connect.return_value.__enter__.return_value = fake_connection
    # create_engine is imported locally inside main() (deferred, not
    # module-level - see the comment there), so it must be patched on
    # sqlalchemy itself rather than on the verify_audit_chain module.
    monkeypatch.setattr("sqlalchemy.create_engine", MagicMock(return_value=fake_engine))
    yield mock


# --- DSN resolution -----------------------------------------------------------------


def test_cli_flag_takes_precedence_over_every_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_AUDIT_VERIFY_DATABASE_URL", "postgresql://verify-env")
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    assert verify._resolve_database_url("postgresql://cli") == "postgresql://cli"


def test_audit_verify_env_var_takes_precedence_over_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_AUDIT_VERIFY_DATABASE_URL", "postgresql://verify-env")
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    assert verify._resolve_database_url(None) == "postgresql://verify-env"


def test_falls_back_to_database_url_when_verify_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    assert verify._resolve_database_url(None) == "postgresql://app-env"


def test_no_dsn_configured_anywhere_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    assert verify._resolve_database_url(None) is None


def test_empty_database_url_flag_is_a_usage_error_not_a_silent_fallthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--database-url ""` must not be treated the same as omitting the flag
    - an operator who typed it almost certainly meant to pin a specific DSN,
    not fall back to whatever the environment happens to say."""
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    with pytest.raises(ValueError, match="--database-url"):
        verify._resolve_database_url("")


def test_unimportable_workspace_during_dsn_resolution_exits_3_not_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plausible operator slip - running this script outside `uv run`/the
    venv - makes `nptc.settings` unimportable. `_resolve_database_url`'s
    deferred import then raises `ImportError`, which is not a `ValueError`
    and so isn't caught by main()'s usage-error handling alone; it must
    still map to exit 3 (main()'s own catch-all around DSN resolution),
    never escape as an unhandled exception - which Python would otherwise
    report via exit 1, coincidentally identical to `EXIT_BROKEN`."""
    monkeypatch.setitem(sys.modules, "nptc.settings", None)
    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)

    exit_code = verify.main([])

    assert exit_code == verify.EXIT_COULD_NOT_COMPLETE


def test_malformed_database_url_env_var_is_distinguishable_from_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whitespace-only NPTC_DATABASE_URL fails DatabaseSettings' own
    non-blank validator - a genuinely malformed value, not simply "no DSN
    configured anywhere" - and must propagate rather than being swallowed
    into the same generic message as an unset variable."""
    from pydantic import ValidationError

    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.setenv("NPTC_DATABASE_URL", "   ")

    with pytest.raises(ValidationError):
        verify._resolve_database_url(None)


def test_main_exits_2_and_names_all_three_sources_when_no_dsn_resolves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)

    exit_code = verify.main([])

    assert exit_code == verify.EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    assert "--database-url" in err
    assert "NPTC_AUDIT_VERIFY_DATABASE_URL" in err
    assert "NPTC_DATABASE_URL" in err


def test_main_exits_2_with_a_distinct_message_for_a_malformed_env_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NPTC_AUDIT_VERIFY_DATABASE_URL", raising=False)
    monkeypatch.setenv("NPTC_DATABASE_URL", "   ")

    exit_code = verify.main([])

    assert exit_code == verify.EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    # Distinct from "no database URL configured" (the unset-anywhere case) -
    # this is "a value was supplied and it's invalid".
    assert "no database URL configured" not in err


def test_resolved_dsn_never_appears_in_stdout_or_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_dsn = "postgresql://user:hunter2@db.example/nptc"
    with _patched_verify_chain(monkeypatch, _ok()):
        exit_code = verify.main(["--database-url", secret_dsn])

    assert exit_code == verify.EXIT_OK
    captured = capsys.readouterr()
    assert secret_dsn not in captured.out
    assert secret_dsn not in captured.err
    assert "hunter2" not in captured.out
    assert "hunter2" not in captured.err


# --- validation -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["not-hex", "a" * 63, "A" * 64, ""])
def test_malformed_expected_head_hash_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    exit_code = verify.main(["--expected-head-hash", value])
    assert exit_code == verify.EXIT_USAGE_ERROR
    assert "--expected-head-hash" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["not-a-number", "-1", "1.5"])
def test_malformed_expected_record_count_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    exit_code = verify.main(["--expected-record-count", value])
    assert exit_code == verify.EXIT_USAGE_ERROR
    assert "--expected-record-count" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "-500"])
def test_non_positive_batch_size_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    """`yield_per`/`fetchmany` treat a size < 1 as `fetchall()` - silently
    discarding the streaming behaviour this module's own docstring promises
    on a large `audit_event` table - so this must be rejected as a usage
    error, the same as the other two validated flags."""
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://app-env")
    exit_code = verify.main(["--batch-size", value])
    assert exit_code == verify.EXIT_USAGE_ERROR
    assert "--batch-size" in capsys.readouterr().err


# --- ChainVerification -> exit code ----------------------------------------------


def test_ok_chain_exits_0_and_reports_count_range_and_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _ok(record_count=3, head_hash=_OK_HASH)):
        exit_code = verify.main(["--database-url", "postgresql://x"])

    assert exit_code == verify.EXIT_OK
    out = capsys.readouterr().out
    assert "3" in out
    assert "1..3" in out
    assert _OK_HASH in out


def test_empty_table_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _ok(record_count=0, head_hash=None)):
        exit_code = verify.main(["--database-url", "postgresql://x"])

    assert exit_code == verify.EXIT_OK
    assert "0" in capsys.readouterr().out


def test_broken_chain_exits_1_and_names_first_broken_sequence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _broken()):
        exit_code = verify.main(["--database-url", "postgresql://x"])

    assert exit_code == verify.EXIT_BROKEN
    # A non-zero exit's explanation belongs on stderr - a cron wrapper
    # tailing only stderr must still see it.
    err = capsys.readouterr().err
    assert "2" in err
    assert "entry_hash mismatch" in err


def test_head_hash_mismatch_exits_4_even_though_chain_verifies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _ok(head_hash=_OK_HASH)):
        exit_code = verify.main(
            ["--database-url", "postgresql://x", "--expected-head-hash", _OTHER_HASH]
        )

    assert exit_code == verify.EXIT_ANCHOR_MISMATCH
    captured = capsys.readouterr()
    assert "ANCHOR MISMATCH" in captured.err
    assert "OK:" in captured.out


def test_record_count_mismatch_exits_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _ok(record_count=3)):
        exit_code = verify.main(
            ["--database-url", "postgresql://x", "--expected-record-count", "5"]
        )

    assert exit_code == verify.EXIT_ANCHOR_MISMATCH
    assert "ANCHOR MISMATCH" in capsys.readouterr().err


def test_empty_table_head_hash_renders_as_none_placeholder_not_python_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _patched_verify_chain(monkeypatch, _ok(record_count=0, head_hash=None)):
        exit_code = verify.main(["--database-url", "postgresql://x"])

    assert exit_code == verify.EXIT_OK
    out = capsys.readouterr().out
    assert "head entry_hash=None" not in out
    assert "(none)" in out


def test_matching_anchor_still_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    with _patched_verify_chain(monkeypatch, _ok(record_count=3, head_hash=_OK_HASH)):
        exit_code = verify.main(
            [
                "--database-url",
                "postgresql://x",
                "--expected-head-hash",
                _OK_HASH,
                "--expected-record-count",
                "3",
            ]
        )

    assert exit_code == verify.EXIT_OK


def test_broken_chain_is_not_also_reported_as_anchor_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A break (exit 1) is distinct from an anchor mismatch (exit 4) - a
    broken chain must never fall through to exit 4's message even when an
    anchor was supplied and would also disagree."""
    with _patched_verify_chain(monkeypatch, _broken()):
        exit_code = verify.main(
            ["--database-url", "postgresql://x", "--expected-head-hash", _OTHER_HASH]
        )

    assert exit_code == verify.EXIT_BROKEN
    assert "ANCHOR MISMATCH" not in capsys.readouterr().err


# --- unexpected failures during verification -> exit 3 --------------------------


def test_could_not_connect_exits_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sqlalchemy.exc import OperationalError

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("connect failed", {}, Exception("boom"))

    monkeypatch.setattr("sqlalchemy.create_engine", MagicMock(side_effect=_raise))

    exit_code = verify.main(["--database-url", "postgresql://unreachable"])

    assert exit_code == verify.EXIT_COULD_NOT_COMPLETE
    assert "could not verify" in capsys.readouterr().err


@pytest.mark.parametrize(
    "exc",
    [
        ModuleNotFoundError("No module named 'psycopg2'"),  # missing DBAPI driver
        ValueError("invalid literal for int() with base 10: 'nope'"),  # bad port
        RuntimeError("connection lost mid-walk"),  # anything else unhandled
    ],
)
def test_any_unexpected_failure_exits_3_never_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: Exception
) -> None:
    """Exit 1 must mean only one thing - a real chain break - never a DSN
    typo, a missing driver, or a dropped connection surfacing as an
    unhandled exception (which Python would otherwise report via exit 1
    itself, coincidentally matching EXIT_BROKEN and misleading a scheduled
    check into reporting a false chain-tamper alert)."""
    monkeypatch.setattr("sqlalchemy.create_engine", MagicMock(side_effect=exc))

    exit_code = verify.main(["--database-url", "postgresql+psycopg://x/y"])

    assert exit_code == verify.EXIT_COULD_NOT_COMPLETE


def test_exit_3_message_does_not_leak_connection_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The underlying exception (e.g. a psycopg OperationalError) can carry
    host/user/dbname in its message - only the exception's type name is
    safe to print (NFR-26/NFR-35), never str(exc) itself."""
    from sqlalchemy.exc import OperationalError

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise OperationalError(
            "connection to server at db.internal.example, port 5432 failed for user secret_admin",
            {},
            Exception("boom"),
        )

    monkeypatch.setattr("sqlalchemy.create_engine", MagicMock(side_effect=_raise))

    exit_code = verify.main(["--database-url", "postgresql://unreachable"])

    assert exit_code == verify.EXIT_COULD_NOT_COMPLETE
    captured = capsys.readouterr()
    assert "db.internal.example" not in captured.err
    assert "secret_admin" not in captured.err
    assert "OperationalError" in captured.err
