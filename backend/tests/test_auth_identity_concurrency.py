"""Two requests resolving the same new `(issuer, subject)` at once (NFR-04).

These tests `commit()` into the shared container, unlike the rolled-back tests in
`test_auth_identity_resolution.py`, so they live apart and request
`pristine_audit_event`, which wipes the user and audit tables before and after.

The race is forced, not hoped for. The winner inserts and holds its transaction
open. The loser then reads (it cannot see the winner's uncommitted row), inserts, and
blocks on the winner's unique key. The test commits the winner only once Postgres
reports a blocked lock, so the loser always takes the recovery path.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import NamedTuple

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.identity import LinkOutcome, resolve_user_for_claims

_ISSUER = "https://idp-race.example"
_OTHER_TRUSTED_ISSUER = "https://idp-race-linked.example"
_TRUSTED = frozenset({_ISSUER, _OTHER_TRUSTED_ISSUER})

_STEP_TIMEOUT = 15.0


def _claims(
    *,
    subject: str,
    issuer: str = _ISSUER,
    email: str | None = None,
    email_verified: bool = False,
    preferred_username: str | None = None,
) -> OidcIdentityClaims:
    return OidcIdentityClaims(
        issuer=issuer,
        subject=subject,
        email=email,
        email_verified=email_verified,
        preferred_username=preferred_username,
        display_name=None,
    )


class Raced(NamedTuple):
    """Read before commit: a committed `User` expires and cannot be read once its
    session closes."""

    outcome: LinkOutcome
    user_id: uuid.UUID


def _wait_for_blocked_insert(owner_engine: Engine) -> None:
    """Returns once some session is waiting on another transaction's row lock."""
    deadline = time.monotonic() + _STEP_TIMEOUT
    while time.monotonic() < deadline:
        with owner_engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'transactionid' AND NOT granted"
                )
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the loser never blocked on the winner's uncommitted insert")


def _race(
    app_engine: Engine, owner_engine: Engine, claims: OidcIdentityClaims
) -> tuple[Raced, Raced]:
    """Resolves `claims` in a winner and a loser session. Returns (winner, loser)."""
    winner_inserted = threading.Event()
    loser_unblocked = threading.Event()
    results: dict[str, Raced] = {}
    errors: dict[str, BaseException] = {}

    def _resolve(session: Session) -> Raced:
        resolution = resolve_user_for_claims(
            session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
        )
        assert resolution.user is not None
        return Raced(resolution.outcome, resolution.user.id)

    def _winner() -> None:
        session = Session(app_engine)
        try:
            results["winner"] = _resolve(session)
            session.flush()
            winner_inserted.set()
            if not loser_unblocked.wait(timeout=_STEP_TIMEOUT):
                raise AssertionError("the loser never reached its blocking insert")
            session.commit()
        except BaseException as exc:  # surfaced by the caller, not swallowed
            session.rollback()
            errors["winner"] = exc
            winner_inserted.set()
        finally:
            session.close()

    def _loser() -> None:
        session = Session(app_engine)
        try:
            if not winner_inserted.wait(timeout=_STEP_TIMEOUT):
                raise AssertionError("the winner never inserted")
            results["loser"] = _resolve(session)
            session.commit()
        except BaseException as exc:  # surfaced by the caller, not swallowed
            session.rollback()
            errors["loser"] = exc
        finally:
            session.close()

    winner = threading.Thread(target=_winner)
    loser = threading.Thread(target=_loser)
    winner.start()
    loser.start()
    try:
        assert winner_inserted.wait(timeout=_STEP_TIMEOUT)
        _wait_for_blocked_insert(owner_engine)
    finally:
        loser_unblocked.set()
        winner.join(timeout=_STEP_TIMEOUT)
        loser.join(timeout=_STEP_TIMEOUT)

    if errors:
        first_exc = next(iter(errors.values()))
        raise AssertionError(f"unexpected exception(s) in racing threads: {errors}") from first_exc
    return results["winner"], results["loser"]


def _scalar(app_engine: Engine, sql: str, **params: object) -> int:
    with Session(app_engine) as session:
        return session.execute(text(sql), params).scalar_one()


def _created_events(app_engine: Engine) -> int:
    """Every `user_identity.created` event in the table. Whole-table is safe here:
    `pristine_audit_event` emptied it, so a loser's rolled-back SAVEPOINT that leaked
    an event would show up as an extra row."""
    return _scalar(
        app_engine, "SELECT count(*) FROM audit_event WHERE action = 'user_identity.created'"
    )


@pytest.mark.req("NFR-04")
@pytest.mark.integration
@pytest.mark.parametrize(
    "preferred_username",
    [None, "race-shared-username"],
    ids=["no-username", "shared-username"],
)
def test_concurrent_first_logins_for_one_new_subject_resolve_to_one_user(
    pristine_audit_event: None,
    app_engine: Engine,
    owner_engine: Engine,
    preferred_username: str | None,
) -> None:
    """The defect behind the 500 on the first page load: both requests missed the
    read, the loser's insert blocked on the winner's key, and the resulting
    `uq_user_identity_issuer` violation reached the client. With a shared
    `preferred_username` the loser first retries a new username suffix, then hits
    the identity constraint, so the recovery must hold on both orders."""
    subject = f"race-first-{uuid.uuid4()}"
    claims = _claims(subject=subject, preferred_username=preferred_username)

    winner, loser = _race(app_engine, owner_engine, claims)

    assert winner.outcome is LinkOutcome.CREATED
    assert loser.outcome is LinkOutcome.EXISTING
    assert loser.user_id == winner.user_id
    assert _scalar(app_engine, "SELECT count(*) FROM app_user") == 1
    assert _scalar(app_engine, "SELECT count(*) FROM user_identity") == 1
    assert _created_events(app_engine) == 1
    assert (
        _scalar(
            app_engine,
            "SELECT count(*) FROM audit_event WHERE action = 'user_role.granted' "
            "AND entity_id IN (SELECT id::text FROM user_role WHERE user_id = :uid)",
            uid=winner.user_id,
        )
        == 1
    )


@pytest.mark.req("NFR-04")
@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_concurrent_auto_links_for_one_new_subject_resolve_to_one_identity(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> None:
    """The auto-link insert had no SAVEPOINT, so the same race aborted the whole
    request transaction. The loser must come back as `EXISTING` on the user the
    winner linked, leaving one identity and one `user_identity.created` event for
    the new subject."""
    email = f"race-link-{uuid.uuid4()}@example.org"
    existing = _claims(subject=f"race-existing-{uuid.uuid4()}", email=email, email_verified=True)
    with Session(app_engine) as session:
        seeded = resolve_user_for_claims(
            session, existing, trusted_issuers=_TRUSTED, audit=AuditContext.system()
        )
        assert seeded.outcome is LinkOutcome.CREATED and seeded.user is not None
        seeded_user_id = seeded.user.id
        session.commit()

    subject = f"race-linked-{uuid.uuid4()}"
    claims = _claims(
        subject=subject,
        issuer=_OTHER_TRUSTED_ISSUER,
        email=email,
        email_verified=True,
    )

    winner, loser = _race(app_engine, owner_engine, claims)

    assert winner.outcome is LinkOutcome.AUTO_LINKED
    assert loser.outcome is LinkOutcome.EXISTING
    assert winner.user_id == loser.user_id == seeded_user_id
    assert _scalar(app_engine, "SELECT count(*) FROM app_user") == 1
    assert (
        _scalar(app_engine, "SELECT count(*) FROM user_identity WHERE subject = :s", s=subject) == 1
    )
    assert _created_events(app_engine) == 2
