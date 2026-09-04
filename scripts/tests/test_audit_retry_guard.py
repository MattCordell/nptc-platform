"""Unit tests for scripts/audit_retry_guard.py (issue #255, PR #258 review).

Regression coverage for the two ways this guard has already been wrong once
in its own history: an early version matched pnpm's own
ERR_PNPM_FETCH_401/403/404 codes (a real, deterministic HTTP-status
failure) as if they were the registry-timeout flake this guard exists for,
and a bash version of this loop had its output silently swallowed by
GitHub's default `bash -e` step shell before the failure could even be
classified. Both get a test here so neither regresses silently again.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit_retry_guard as guard


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout = iter(lines)
        self._returncode = returncode
        self.returncode: int | None = None

    def wait(self) -> int:
        self.returncode = self._returncode
        return self._returncode


def _popen_sequence(responses: list[tuple[list[str], int]]) -> Callable[..., FakeProcess]:
    calls = iter(responses)

    def popen(*_args: object, **_kwargs: object) -> FakeProcess:
        lines, returncode = next(calls)
        return FakeProcess(lines, returncode)

    return popen


# --- is_registry_timeout ---------------------------------------------------------------


@pytest.mark.parametrize(
    "output",
    [
        "TimeoutError: The operation was aborted due to timeout\n",
        "[23] The operation was aborted due to timeout\n",
        "request to https://registry.npmjs.org/ failed, reason: connect ETIMEDOUT\n",
        "FetchError: request failed, reason: read ECONNRESET\n",
        "connect ECONNREFUSED 127.0.0.1:443\n",
        "getaddrinfo ENOTFOUND registry.npmjs.org\n",
        "getaddrinfo EAI_AGAIN registry.npmjs.org\n",
    ],
)
def test_recognises_network_signatures(output: str) -> None:
    assert guard.is_registry_timeout(output) is True


@pytest.mark.parametrize(
    "output",
    [
        "1 vulnerabilities found\nSeverity: high\nPrototype Pollution in some-package\n",
        " ERR_PNPM_FETCH_401  GET https://registry.npmjs.org/...: Unauthorized - 401\n",
        " ERR_PNPM_FETCH_403  GET https://registry.npmjs.org/...: Forbidden - 403\n",
        " ERR_PNPM_FETCH_404  GET https://registry.npmjs.org/...: Not Found - 404\n",
        "No known vulnerabilities found\n",
    ],
)
def test_does_not_mistake_real_failures_for_network_flakes(output: str) -> None:
    assert guard.is_registry_timeout(output) is False


# --- run_with_retries --------------------------------------------------------------------


def test_success_exits_zero_after_one_attempt() -> None:
    out = io.StringIO()
    popen = _popen_sequence([(["No known vulnerabilities found\n"], 0)])

    code = guard.run_with_retries(["pnpm", "audit"], popen=popen, sleep=lambda _: None, out=out)

    assert code == 0
    assert "No known vulnerabilities found" in out.getvalue()


def test_real_advisory_fails_immediately_with_no_retry() -> None:
    out = io.StringIO()
    call_count = 0
    inner_popen = _popen_sequence([(["1 vulnerabilities found\n", "Severity: high\n"], 1)])

    def counting_popen(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal call_count
        call_count += 1
        return inner_popen(*args, **kwargs)

    code = guard.run_with_retries(
        ["pnpm", "audit"], popen=counting_popen, sleep=lambda _: None, out=out
    )

    assert code == 1
    assert call_count == 1
    assert "1 vulnerabilities found" in out.getvalue()


def test_http_status_failure_fails_immediately_with_no_retry() -> None:
    """The regression this guard's regex has already caused once: an
    ERR_PNPM_FETCH_401/403/404 is a real response (bad auth, not found),
    not a dead connection, and retrying it wastes the job's retry budget on
    something no amount of waiting fixes."""
    out = io.StringIO()
    call_count = 0
    inner_popen = _popen_sequence([([" ERR_PNPM_FETCH_401  Unauthorized\n"], 1)])

    def counting_popen(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal call_count
        call_count += 1
        return inner_popen(*args, **kwargs)

    code = guard.run_with_retries(
        ["pnpm", "audit"], popen=counting_popen, sleep=lambda _: None, out=out
    )

    assert code == 1
    assert call_count == 1


def test_registry_timeout_retries_once_then_fails_with_error_annotation() -> None:
    out = io.StringIO()
    sleeps: list[float] = []
    popen = _popen_sequence(
        [
            (["TimeoutError: The operation was aborted due to timeout\n"], 1),
            (["TimeoutError: The operation was aborted due to timeout\n"], 1),
        ]
    )

    code = guard.run_with_retries(
        ["pnpm", "audit"],
        max_attempts=2,
        sleep_seconds=30,
        popen=popen,
        sleep=sleeps.append,
        out=out,
    )

    assert code == 1
    assert sleeps == [30]
    assert out.getvalue().count("TimeoutError") == 2
    assert "::error::pnpm audit failed after 2 attempts" in out.getvalue()
    assert "registry.npmjs.org appears unreachable (see issue #255)" in out.getvalue()


def test_registry_timeout_recovers_on_retry() -> None:
    out = io.StringIO()
    popen = _popen_sequence(
        [
            (["TimeoutError: The operation was aborted due to timeout\n"], 1),
            (["No known vulnerabilities found\n"], 0),
        ]
    )

    code = guard.run_with_retries(
        ["pnpm", "audit"], max_attempts=2, popen=popen, sleep=lambda _: None, out=out
    )

    assert code == 0
    assert "No known vulnerabilities found" in out.getvalue()


def test_output_streams_line_by_line_not_only_at_exit() -> None:
    """The buffering complaint this guard's bash predecessor drew: a run
    sitting in retry backoff should still show progress in the log."""
    written: list[str] = []

    class RecordingWriter(io.StringIO):
        def write(self, s: str) -> int:
            written.append(s)
            return super().write(s)

    out = RecordingWriter()
    popen = _popen_sequence([(["line one\n", "line two\n"], 0)])

    guard.run_with_retries(["pnpm", "audit"], popen=popen, sleep=lambda _: None, out=out)

    assert written == ["line one\n", "line two\n"]


# --- main --------------------------------------------------------------------------------


def test_main_strips_leading_double_dash_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_with_retries(command: list[str], **_kwargs: object) -> int:
        captured["command"] = command
        return 0

    monkeypatch.setattr(guard, "run_with_retries", fake_run_with_retries)

    code = guard.main(["--", "pnpm", "audit", "--prod"])

    assert code == 0
    assert captured["command"] == ["pnpm", "audit", "--prod"]


def test_main_with_no_command_prints_usage_and_returns_2() -> None:
    assert guard.main([]) == 2
