"""SQLAlchemy models, the engine and session factory, and the least-privilege roles.

`base.py` holds the declarative Base and naming convention, `roles.py` the least-privilege app role
and its grant/revoke SQL, `models/` the ORM models, and `session.py` the engine and per-request
session factory. The Alembic environment is `backend/migrations/env.py`, outside this package
because Alembic expects `env.py` inside the configured `script_location` (see `[tool.alembic]` in
the root `pyproject.toml`).
"""
