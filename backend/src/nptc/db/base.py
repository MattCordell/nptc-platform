"""Declarative base and naming convention shared by every SQLAlchemy model.

A named `MetaData` convention makes Alembic autogenerate produce deterministic constraint and index
names. Without it Postgres assigns anonymous names to check constraints and driver-dependent
suffixes to indexes, so one model can autogenerate different names on two runs, and a
downgrade/upgrade round-trip cannot find the constraint to drop.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: The standard SQLAlchemy convention. `docs/architecture/data-model.md` covers the 63-character
#: Postgres identifier truncation it can still hit on a long table or column name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model. `Base.metadata` is what `backend/migrations/env.py`
    targets for autogenerate, so every model must be reachable through `nptc.db.models`.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
