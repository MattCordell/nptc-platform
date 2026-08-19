"""SQLAlchemy models and the Alembic environment (issue #33/P1-1).

``base.py`` (the declarative Base and naming convention), ``roles.py`` (the
least-privilege app role and its grant/revoke SQL) and ``models/`` (ORM
models, starting with the minimal ``audit_event`` table) live here. The
Alembic environment itself is ``backend/migrations/env.py`` - kept outside
this package because Alembic's own tooling expects ``env.py`` inside the
configured ``script_location`` (see ``[tool.alembic]`` in the root
``pyproject.toml``).

``session.py`` holds the engine and per-request session factory. It was
deliberately absent until issue #41 gave it its first consumer
(``nptc.api.dependencies.get_session``) - an untested engine factory would
have been pure drag on the coverage floor before then.
"""
