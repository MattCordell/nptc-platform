"""The engine and session factory the API request path runs on (issue #41).

Deliberately absent until now: ``db/__init__.py`` and ADR-0016's "Scope"
both record that an untested engine factory would have been pure drag on
the coverage floor while nothing consumed it. #41 is the first consumer -
``nptc.api.dependencies.get_session``.

Two things here are load-bearing rather than incidental:

- **``isolation_level="READ COMMITTED"`` is set explicitly**, not left to
  the server default. ``nptc.audit.writer.append_audit_event`` raises
  ``AuditIsolationLevelError`` under anything stricter, because its
  advisory lock only serialises appends correctly at READ COMMITTED (see
  that module's step 1a). The server default *is* READ COMMITTED today,
  but a deployment that changed ``default_transaction_isolation`` in
  ``postgresql.conf`` would otherwise break every audited write path at
  runtime rather than here, at configuration time.
- **``expire_on_commit=False``**. A committed ``Principal``'s ``UserRef``
  is built from a ``User`` instance; with the default ``True`` every
  attribute access after the request's commit would re-issue a SELECT on
  a session that is about to close.

The engine is created once per process and cached: a new engine per
request would mean a new connection pool per request.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from nptc.settings import DatabaseSettings

#: See the module docstring - not a default worth inheriting silently.
REQUIRED_ISOLATION_LEVEL = "READ COMMITTED"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine, built from ``NPTC_DATABASE_URL``.

    ``pool_pre_ping`` because the API is long-lived and sits behind a
    connection-killing proxy/database restart at some point in any real
    deployment; a stale pooled connection should cost one retry, not one
    500.
    """
    settings = DatabaseSettings()
    return create_engine(
        settings.database_url,
        isolation_level=REQUIRED_ISOLATION_LEVEL,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    """One session per request, committed on success and rolled back on
    any exception.

    The commit is here rather than in each route so that a state change
    and the ``audit_event`` row recording it commit together, atomically -
    the property ``append_audit_event`` is written to assume (it takes no
    commit of its own precisely so the caller can make this guarantee).
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
