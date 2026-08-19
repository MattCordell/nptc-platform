"""Integration tests for scripts/grant_role.py (issue #44, FR-01, FR-44):
drives the real CLI end to end against a testcontainers Postgres,
exercising `main()` exactly as an operator would - a real DSN, a
connection this test does not control, and the process's own exit code.

Same real-commit convention as test_verify_audit_chain_cli.py: `db`/
`app_db` wrap each test in a rolled-back outer transaction, so a
*separate* connection (which is what `grant_role.main()` opens from the
DSN it is given) would never see uncommitted rows. Every write here goes
through a real, committed connection instead, with explicit cleanup.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import grant_role


@pytest.fixture
def app_url(owner_engine: Engine, app_login_credentials: tuple[str, str], migrated: None) -> str:
    username, password = app_login_credentials
    login_url = owner_engine.url.set(username=username, password=password)
    return login_url.render_as_string(hide_password=False)


@pytest.fixture
def seeded_user(owner_engine: Engine, migrated: None) -> Iterator[str]:
    """A committed `app_user` row the CLI can look up by username - real
    commits, since `grant_role.main()`'s own connection must see it."""
    username = "grant-role-cli-test-user"
    with owner_engine.connect() as connection:
        connection.execute(
            text("INSERT INTO app_user (username, display_name) VALUES (:username, :username)"),
            {"username": username},
        )
        connection.commit()
    try:
        yield username
    finally:
        with owner_engine.connect() as connection:
            connection.execute(text("DELETE FROM user_role"))
            connection.execute(text("DELETE FROM audit_event"))
            connection.execute(text("DELETE FROM app_user"))
            connection.commit()


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_grants_administrator_to_a_real_user_and_records_an_audit_event(
    owner_engine: Engine, app_url: str, seeded_user: str, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = grant_role.main(
        ["--username", seeded_user, "--role", "administrator", "--database-url", app_url]
    )

    assert exit_code == grant_role.EXIT_OK
    assert "granted 'administrator'" in capsys.readouterr().out

    with owner_engine.connect() as connection:
        role_row = connection.execute(
            text(
                "SELECT ur.role, ur.granted_by_user_id FROM user_role ur "
                "JOIN app_user u ON u.id = ur.user_id WHERE u.username = :username"
            ),
            {"username": seeded_user},
        ).one()
        assert role_row.role == "administrator"
        assert role_row.granted_by_user_id is None

        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_event WHERE action = 'user_role.granted'")
        ).scalar_one()
        assert audit_count == 1


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_reports_not_found_for_an_unknown_username(app_url: str, migrated: None) -> None:
    exit_code = grant_role.main(
        ["--username", "no-such-user-at-all", "--role", "observer", "--database-url", app_url]
    )

    assert exit_code == grant_role.EXIT_NOT_FOUND


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_granting_the_same_role_twice_is_idempotent(
    app_url: str, seeded_user: str, capsys: pytest.CaptureFixture[str]
) -> None:
    first = grant_role.main(
        ["--username", seeded_user, "--role", "reviewer", "--database-url", app_url]
    )
    second = grant_role.main(
        ["--username", seeded_user, "--role", "reviewer", "--database-url", app_url]
    )

    assert first == grant_role.EXIT_OK
    assert second == grant_role.EXIT_OK
