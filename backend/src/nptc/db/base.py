"""Declarative base and naming convention shared by every SQLAlchemy model.

A named ``MetaData`` convention is what makes Alembic autogenerate produce
deterministic constraint/index names - without it, Postgres assigns
anonymous names for check constraints and driver-dependent suffixes for
indexes, and the same model can autogenerate a different constraint name on
two different runs depending on declaration order, breaking a
downgrade/upgrade round-trip's ability to find the constraint to drop.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: Standard SQLAlchemy convention (recommended in its own documentation).
#: See docs/architecture/data-model.md for the 63-character Postgres
#: identifier truncation this can still hit on a long table/column name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model.

    ``Base.metadata`` is what ``backend/migrations/env.py`` targets for
    autogenerate, so every model must be reachable through
    ``nptc.db.models`` for autogenerate to see it.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
