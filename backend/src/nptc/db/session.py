"""The engine and session factory the API request path runs on (`nptc.api.dependencies.get_session`
is the consumer).

Two things here are load-bearing:

- **`isolation_level="READ COMMITTED"` is set explicitly**, not left to the server default.
  `nptc.audit.writer.append_audit_event` raises `AuditIsolationLevelError` under anything stricter,
  because its advisory lock serialises appends correctly only at READ COMMITTED (see that module's
  step 1a). A deployment that changed `default_transaction_isolation` would otherwise break every
  audited write at runtime rather than at configuration time.
- **`expire_on_commit=False`.** A committed `Principal`'s `UserRef` is built from a `User` instance;
  with the default `True`, every attribute access after the request's commit would re-issue a SELECT
  on a session about to close.

The engine is created once per process and cached, because a new engine per request would mean a new
connection pool per request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from nptc.settings import DatabaseSettings

#: See the module docstring - not a default worth inheriting silently.
REQUIRED_ISOLATION_LEVEL = "READ COMMITTED"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine, built from `NPTC_DATABASE_URL`. `pool_pre_ping` because the API is
    long-lived and meets a connection-killing proxy or database restart at some point; a stale
    pooled connection should cost one retry, not one 500.
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


_POST_COMMIT_KEY = "nptc.post_commit_actions"

_log = logging.getLogger(__name__)


def after_commit(session: Session, action: Callable[[], None]) -> None:
    """Queues `action` to run once, after `session_scope` has committed this session. A request
    that rolls back discards it.

    Not FastAPI's `BackgroundTasks`: those run while the request's session is still open, before
    its commit, so a task that reads what the request just wrote sees the old rows.

    `action` must not use `session`, and must return quickly: it runs on the request's thread
    after the response has been sent.
    """
    session.info.setdefault(_POST_COMMIT_KEY, []).append(action)


def discard_after_commit_actions(session: Session) -> None:
    session.info.pop(_POST_COMMIT_KEY, None)


def run_after_commit_actions(session: Session) -> None:
    """Runs and clears the queued actions. One that raises is logged and does not stop the rest,
    because the commit has already happened and cannot be undone by failing the request.
    """
    for action in session.info.pop(_POST_COMMIT_KEY, []):
        try:
            action()
        except Exception as exc:
            _log.error("a post-commit action failed (%s)", type(exc).__name__)


def session_scope() -> Iterator[Session]:
    """One session per request, committed on success and rolled back on any exception. The commit
    lives here, not in each route, so a state change and the `audit_event` row recording it commit
    atomically, which `append_audit_event` assumes (it takes no commit of its own).

    Actions queued with `after_commit` run after the commit.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        discard_after_commit_actions(session)
        raise
    else:
        run_after_commit_actions(session)
    finally:
        session.close()
