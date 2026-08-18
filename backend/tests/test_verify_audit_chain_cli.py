"""Integration tests for scripts/verify_audit_chain.py (issue #38, NFR-10,
NFR-38 test 5): drives the real CLI end to end against a testcontainers
Postgres, exercising `main()` exactly as an operator would - a real DSN, a
connection this test does not control, and the process's own exit code.

Unlike `test_audit_chain.py`/`test_audit_tamper_detection.py`, this module
cannot use the `db`/`app_db` fixtures for the writes under test: those wrap
each test in an outer transaction rolled back at teardown, so a *separate*
connection - which is what `verify_audit_chain.main()` opens from the DSN it
is given - would never see the uncommitted rows. Every write here goes
through a real, committed connection instead, with an explicit
`audit_event`/`app_user` cleanup at the end of each test.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext, append_audit_event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import verify_audit_chain as verify


@pytest.fixture
def owner_url(owner_engine: Engine, migrated: None) -> str:
    # str(URL) masks the password with `***` (SQLAlchemy's safe-repr
    # default) - render_as_string(hide_password=False) is required to get a
    # DSN verify_audit_chain.main() can actually connect with.
    return owner_engine.url.render_as_string(hide_password=False)


@pytest.fixture
def app_url(owner_engine: Engine, app_login_credentials: tuple[str, str], migrated: None) -> str:
    """The `nptc_app_login` DSN - proves the CLI verifies without the
    write role, an explicit acceptance criterion."""
    username, password = app_login_credentials
    login_url = owner_engine.url.set(username=username, password=password)
    return login_url.render_as_string(hide_password=False)


@pytest.fixture
def clean_audit_event(owner_engine: Engine, migrated: None) -> Iterator[None]:
    """Commits nothing itself - just guarantees every row this module writes
    is gone afterwards, real commits and all, so other integration modules
    never see cross-test leftovers."""
    try:
        yield
    finally:
        with owner_engine.connect() as connection:
            connection.execute(text("DELETE FROM audit_event"))
            connection.execute(text("DELETE FROM app_user"))
            connection.commit()


def _append_three_committed(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection:
        session = Session(bind=connection)
        for entity_id in ("1", "2", "3"):
            append_audit_event(
                session,
                AuditContext.system(),
                action="test.action",
                entity_type="test_entity",
                entity_id=entity_id,
            )
        session.flush()
        session.commit()


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_intact_chain_exits_0_over_the_app_role(
    owner_engine: Engine, app_url: str, clean_audit_event: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _append_three_committed(owner_engine)

    exit_code = verify.main(["--database-url", app_url])

    assert exit_code == verify.EXIT_OK
    out = capsys.readouterr().out
    assert "3" in out


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_empty_table_exits_0(
    owner_url: str, clean_audit_event: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = verify.main(["--database-url", owner_url])

    assert exit_code == verify.EXIT_OK
    assert "0" in capsys.readouterr().out


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_corrupted_middle_row_exits_1_naming_the_first_broken_sequence(
    owner_engine: Engine,
    owner_url: str,
    clean_audit_event: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _append_three_committed(owner_engine)

    with owner_engine.connect() as connection:
        middle = connection.execute(
            text("SELECT id, sequence FROM audit_event ORDER BY sequence LIMIT 1 OFFSET 1")
        ).one()
        connection.execute(
            text("UPDATE audit_event SET entity_id = 'tampered' WHERE id = :id"),
            {"id": middle.id},
        )
        connection.commit()

    exit_code = verify.main(["--database-url", owner_url])

    assert exit_code == verify.EXIT_BROKEN
    out = capsys.readouterr().out
    assert str(middle.sequence) in out
    assert "entry_hash mismatch" in out


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_truncated_tail_verifies_ok_alone_but_fails_the_supplied_anchor(
    owner_engine: Engine,
    owner_url: str,
    clean_audit_event: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The known gap ADR-0017/hazard H-06 name: deleting the most recent row
    leaves a chain that still verifies `ok` on its own - only an
    operator-supplied `--expected-head-hash`/`--expected-record-count`
    catches it."""
    _append_three_committed(owner_engine)

    with owner_engine.connect() as connection:
        original_head = connection.execute(
            text("SELECT entry_hash FROM audit_event ORDER BY sequence DESC LIMIT 1")
        ).scalar_one()

    without_anchor = verify.main(["--database-url", owner_url])
    assert without_anchor == verify.EXIT_OK

    with owner_engine.connect() as connection:
        connection.execute(
            text("DELETE FROM audit_event WHERE sequence = (SELECT max(sequence) FROM audit_event)")
        )
        connection.commit()

    exit_code = verify.main(
        [
            "--database-url",
            owner_url,
            "--expected-head-hash",
            original_head,
            "--expected-record-count",
            "3",
        ]
    )

    assert exit_code == verify.EXIT_ANCHOR_MISMATCH
    assert "ANCHOR MISMATCH" in capsys.readouterr().out


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_could_not_connect_exits_3() -> None:
    bogus_url = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/does-not-exist"

    exit_code = verify.main(["--database-url", bogus_url])

    assert exit_code == verify.EXIT_COULD_NOT_COMPLETE
