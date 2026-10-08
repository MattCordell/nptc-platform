"""`nptc.db.session.after_commit` (FR-13): work queued during a request runs only once the
request's session has committed, and never when it rolled back.

The first group needs no container. The second runs the real `session_scope` under a real FastAPI
route against the test database, because the ordering is a fact about FastAPI and Starlette, not
about this module: `test_background_tasks_run_before_the_session_commits` records the ordering that
made `BackgroundTasks` the wrong tool, so a framework change that alters it fails here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from testcontainers.community.postgres import PostgresContainer

from nptc.api.dependencies import get_session
from nptc.db import session as session_module
from nptc.db.models.property_definition import (
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.db.session import (
    after_commit,
    discard_after_commit_actions,
    run_after_commit_actions,
    session_scope,
)


@pytest.fixture
def scoped_sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`session_scope` on an in-memory SQLite database: its commit and rollback behaviour does not
    depend on the database."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(
        session_module,
        "get_sessionmaker",
        lambda: sessionmaker(bind=engine, expire_on_commit=False),
    )
    yield
    engine.dispose()


def _drive(*, fail: bool) -> list[str]:
    """Runs `session_scope` the way FastAPI does: advance to the yield, then finish or throw."""
    ran: list[str] = []
    scope = session_scope()
    session = next(scope)
    after_commit(session, lambda: ran.append("action"))
    ran.append("body")
    if fail:
        with pytest.raises(RuntimeError):
            scope.throw(RuntimeError("route failed"))
    else:
        with pytest.raises(StopIteration):
            next(scope)
    return ran


@pytest.mark.req("FR-13")
def test_an_action_runs_after_the_body_when_the_request_commits(scoped_sessions: None) -> None:
    assert _drive(fail=False) == ["body", "action"]


@pytest.mark.req("FR-13")
def test_an_action_is_dropped_when_the_request_rolls_back(scoped_sessions: None) -> None:
    assert _drive(fail=True) == ["body"]


@pytest.mark.req("FR-13")
def test_a_failing_action_is_logged_by_type_and_does_not_stop_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = Session()
    ran: list[str] = []

    def refuse() -> None:
        raise ValueError("secret detail that must not be logged")

    after_commit(session, refuse)
    after_commit(session, lambda: ran.append("second"))

    with caplog.at_level(logging.ERROR, logger=session_module.__name__):
        run_after_commit_actions(session)

    assert ran == ["second"]
    assert "ValueError" in caplog.text
    assert "secret detail" not in caplog.text


@pytest.mark.req("FR-13")
def test_actions_run_once(scoped_sessions: None) -> None:
    session = Session()
    ran: list[str] = []
    after_commit(session, lambda: ran.append("once"))

    run_after_commit_actions(session)
    run_after_commit_actions(session)

    assert ran == ["once"]


def test_discarding_with_nothing_queued_is_harmless() -> None:
    discard_after_commit_actions(Session())


# --- against FastAPI and the test database ---------------------------------


@pytest.fixture
def committed_visibility(
    postgres_container: PostgresContainer,
    owner_engine: Engine,
    app_login_credentials: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], bool]]:
    """Points `session_scope` at the test database as the app login, with real commits, and
    returns a probe that says whether a property key is visible to an independent connection.
    Every key the probe is asked about is deleted afterwards."""
    role, password = app_login_credentials
    login_url = make_url(postgres_container.get_connection_url()).set(
        username=role, password=password
    )
    monkeypatch.setenv("NPTC_DATABASE_URL", login_url.render_as_string(hide_password=False))
    session_module.get_engine.cache_clear()
    session_module.get_sessionmaker.cache_clear()
    probed: set[str] = set()

    def visible(key: str) -> bool:
        probed.add(key)
        with owner_engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM property_definition WHERE key = :k"), {"k": key}
            ).scalar_one()
        return bool(count)

    yield visible
    with owner_engine.begin() as connection:
        for key in probed:
            connection.execute(text("DELETE FROM property_definition WHERE key = :k"), {"k": key})
    session_module.get_engine.cache_clear()
    session_module.get_sessionmaker.cache_clear()


def _app(observed: dict[str, bool], visible: Callable[[str], bool]) -> FastAPI:
    """Two routes that write one property definition through the real `get_session`, and record
    whether an independent connection could see it when the follow-up work ran."""
    app = FastAPI()

    def write(key: str, session: Session) -> None:
        session.add(
            PropertyDefinition(
                key=key,
                label=key,
                datatype="string",
                cardinality=PropertyCardinality.ZERO_OR_ONE,
                scope=PropertyScope.BOTH,
                required_for_submission=False,
                required_for_publication=False,
                filterable=False,
                origin=PropertyOrigin.ADMIN,
                display_order=0,
                constraints={},
            )
        )
        session.flush()

    @app.post("/after-commit/{key}")
    def with_after_commit(key: str, session: Annotated[Session, Depends(get_session)]) -> None:
        write(key, session)
        after_commit(session, lambda: observed.__setitem__("after_commit", visible(key)))

    @app.post("/background/{key}")
    def with_background_task(
        key: str, session: Annotated[Session, Depends(get_session)], tasks: BackgroundTasks
    ) -> None:
        write(key, session)
        tasks.add_task(lambda: observed.__setitem__("background", visible(key)))

    return app


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_an_after_commit_action_sees_the_committed_row(
    committed_visibility: Callable[[str], bool],
) -> None:
    observed: dict[str, bool] = {}
    key = f"after_commit_{uuid.uuid4().hex[:8]}"

    with TestClient(_app(observed, committed_visibility)) as client:
        assert client.post(f"/after-commit/{key}").status_code == 200

    assert observed == {"after_commit": True}


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_background_tasks_run_before_the_session_commits(
    committed_visibility: Callable[[str], bool],
) -> None:
    """The reason `after_commit` exists. FastAPI closes a request-scoped `yield` dependency, which
    is where `session_scope` commits, after the response and its background tasks have finished."""
    observed: dict[str, bool] = {}
    key = f"background_{uuid.uuid4().hex[:8]}"

    with TestClient(_app(observed, committed_visibility)) as client:
        assert client.post(f"/background/{key}").status_code == 200

    assert observed == {"background": False}
    assert committed_visibility(key) is True
