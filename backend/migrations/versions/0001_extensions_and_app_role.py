"""extensions and app role

Revision ID: 0001
Revises:
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
    op.execute(roles.CREATE_APP_ROLE_SQL)
    op.execute(roles.GRANT_SCHEMA_USAGE_SQL)


def downgrade() -> None:
    # Deliberately does NOT DROP ROLE: roles are cluster-wide, and dropping
    # one that still holds privileges in any other database in the cluster
    # errors, which would make `downgrade base` fail on a shared cluster. A
    # role is not schema, so the round-trip criterion (schema equality after
    # downgrade -> upgrade) still holds without it - see docs/operations/
    # upgrade.md and ADR-0011 for this stated, deliberate asymmetry.
    op.execute("DROP EXTENSION IF EXISTS unaccent;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
