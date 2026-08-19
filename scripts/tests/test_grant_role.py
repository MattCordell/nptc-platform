"""Offline unit tests for scripts/grant_role.py (issue #44, FR-01, FR-44):
argument parsing and DSN resolution precedence. No Docker/Postgres here -
the real grant, including the last-administrator guard and the audit
event it emits, is exercised by backend/tests/test_grants.py and the
bootstrap runbook check in docs/operations/upgrade.md instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import grant_role


def test_grantable_role_choices_match_the_permission_frameworks_grantable_roles() -> None:
    """The argparse `choices` list is written out by hand (see the
    module's own comment on why: --help must stay usable even if the
    workspace import fails) - this is what keeps it from silently
    drifting away from `nptc.auth.permissions.GRANTABLE_ROLES`."""
    from nptc.auth.permissions import GRANTABLE_ROLES

    assert set(grant_role._GRANTABLE_ROLE_CHOICES) == {role.value for role in GRANTABLE_ROLES}


def test_parse_args_requires_username_and_role() -> None:
    with pytest.raises(SystemExit):
        grant_role._parse_args([])


def test_parse_args_rejects_ungrantable_role() -> None:
    with pytest.raises(SystemExit):
        grant_role._parse_args(["--username", "jsmith", "--role", "anon"])


def test_parse_args_accepts_a_grantable_role() -> None:
    args = grant_role._parse_args(["--username", "jsmith", "--role", "administrator"])
    assert args.username == "jsmith"
    assert args.role == "administrator"
    assert args.database_url is None


def test_resolve_database_url_prefers_explicit_cli_value() -> None:
    assert grant_role._resolve_database_url("postgresql://explicit") == "postgresql://explicit"


def test_resolve_database_url_rejects_explicit_empty_string() -> None:
    """An operator who typed `--database-url ""` almost certainly meant to
    pin a specific DSN - falling through to the environment instead would
    silently run against the wrong database."""
    with pytest.raises(ValueError, match="--database-url must not be empty"):
        grant_role._resolve_database_url("")


def test_resolve_database_url_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "postgresql://from-env")
    assert grant_role._resolve_database_url(None) == "postgresql://from-env"


def test_resolve_database_url_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    assert grant_role._resolve_database_url(None) is None


def test_main_reports_usage_error_when_no_database_url_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    exit_code = grant_role.main(["--username", "jsmith", "--role", "administrator"])
    assert exit_code == grant_role.EXIT_USAGE_ERROR
