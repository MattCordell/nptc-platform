"""Offline unit tests for backend/migrations/env.py (issue #33).

No container, no network, no `db`/`app_db` fixture requested - these two
behaviours ADR-0011 argues hardest for (no fallback to `sqlalchemy.url`,
and the `fileConfig()` guard for a phantom `alembic.ini`) fail before any
real database connection is ever attempted, since `MigrationSettings()`
validation runs first. `backend/migrations` isn't in
`[tool.coverage.run] source`, so nothing else exercises either branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"


def test_env_py_raises_naming_the_variable_when_migration_url_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `config.attributes["connection"]` and no NPTC_MIGRATION_DATABASE_URL:
    env.py must fail loudly naming the missing variable, never fall back to
    Alembic's own placeholder `sqlalchemy.url`."""
    monkeypatch.delenv("NPTC_MIGRATION_DATABASE_URL", raising=False)
    config = Config(toml_file=str(PYPROJECT_FILE))

    with pytest.raises(ValidationError, match="migration_database_url"):
        command.upgrade(config, "head")


def test_env_py_skips_fileconfig_when_the_cli_style_ini_path_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces exactly what the real `alembic` CLI builds when no
    `alembic.ini` is on disk: a `Config` whose `file_` is still the literal
    string `"alembic.ini"` (the CLI's own default), which does not exist as
    a file anywhere in this repo. Without env.py's guard,
    `fileConfig("alembic.ini")` would raise on that nonexistent path before
    ever reaching the DSN check - a different, unrelated exception that
    would mask the assertion below. With the guard, fileConfig() is skipped
    and the assertion below is the *only* thing that raises.
    """
    assert not (REPO_ROOT / "alembic.ini").exists()
    monkeypatch.delenv("NPTC_MIGRATION_DATABASE_URL", raising=False)
    config = Config(file_="alembic.ini", toml_file=str(PYPROJECT_FILE))

    with pytest.raises(ValidationError, match="migration_database_url"):
        command.upgrade(config, "head")
