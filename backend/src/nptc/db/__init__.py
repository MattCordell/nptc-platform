"""SQLAlchemy models and the Alembic environment (issue #33/P1-1).

``base.py`` (the declarative Base and naming convention), ``roles.py`` (the
least-privilege app role and its grant/revoke SQL) and ``models/`` (ORM
models, starting with the minimal ``audit_event`` table) live here. The
Alembic environment itself is ``backend/migrations/env.py`` - kept outside
this package because Alembic's own tooling expects ``env.py`` inside the
configured ``script_location`` (see ``[tool.alembic]`` in the root
``pyproject.toml``).

Session management (``session.py``) is deliberately not here yet - nothing
needs it until #43/#44, and an untested engine factory would be pure drag
on the coverage floor.
"""
