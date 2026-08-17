"""Migration acceptance criteria that don't need a dedicated database
(issue #33) - see backend/tests/test_db_round_trip.py for the
downgrade/upgrade fingerprint test, which does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from alembic.autogenerate.api import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection

from nptc.db.base import Base
from nptc.db.models import (  # noqa: F401 -- import for side effect: populates Base.metadata
    AuditEvent,
    User,
    UserIdentity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.yml"
_VERSION_PREFIX_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*")


@pytest.mark.integration
def test_upgrade_head_matches_models(db: Connection) -> None:
    """`upgrade head` is clean on a fresh DB: nobody edited a model without
    generating a migration for it. `compare_metadata` is blind to
    extensions and grants - see test_db_round_trip.py for those."""
    context = MigrationContext.configure(db)

    diff = compare_metadata(context, Base.metadata)

    assert diff == []


@pytest.mark.integration
def test_pg_trgm_and_unaccent_extensions_are_installed(db: Connection) -> None:
    rows = db.execute(text("SELECT extname FROM pg_extension")).scalars().all()

    assert "pg_trgm" in rows
    assert "unaccent" in rows


@pytest.mark.req("NFR-39")
@pytest.mark.integration
def test_real_postgres_container_not_a_substitute(db: Connection) -> None:
    """Asserts the version string names the numeric version deploy/compose.yml
    pins - proof this is a real, specifically-versioned Postgres server, not
    an in-memory substitute or a different version entirely. Compares only
    the leading numeric prefix of the tag (`18.4-alpine` -> `18.4`, `18` ->
    `18`), not the whole tag verbatim - `postgres --version`-style output
    never repeats a `-alpine`/`-bookworm` suffix, so a literal substring
    match would fail the moment compose.yml pins a valid tag carrying one,
    even though the version itself still matches."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    image: str = compose["services"]["postgres"]["image"]
    tag = image.split(":", 1)[1]
    match = _VERSION_PREFIX_RE.match(tag)
    assert match is not None, f"compose Postgres tag {tag!r} has no numeric version prefix"

    version = db.execute(text("SELECT version()")).scalar_one()

    assert match.group() in version
