"""`user_identity` audit emit sites (issue #163, NFR-08): created on login
(first login and the auto-link path), refreshed on a repeat login that
actually changes something, and deleted on account closure.

`Session(bind=app_db)` joins the existing testcontainers fixture
connection - see test_auth_identity_resolution.py's module docstring for
why.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.identity import close_account, resolve_user_for_claims
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity

_TRUSTED_A = "https://good.example"
_TRUSTED_B = "https://good2.example"
_TRUSTED = frozenset({_TRUSTED_A, _TRUSTED_B})
_UNTRUSTED = "https://untrusted.example"


def _claims(
    *,
    issuer: str,
    subject: str,
    email: str | None = None,
    email_verified: bool = False,
    preferred_username: str | None = None,
    display_name: str | None = None,
) -> OidcIdentityClaims:
    return OidcIdentityClaims(
        issuer=issuer,
        subject=subject,
        email=email,
        email_verified=email_verified,
        preferred_username=preferred_username,
        display_name=display_name,
    )


def _events_for_action(
    session: Session, entity_id: uuid.UUID | str, action: str
) -> list[dict[str, object]]:
    rows = (
        session.execute(
            text(
                "SELECT action, entity_type, entity_id, before, after "
                "FROM audit_event WHERE entity_id = :entity_id AND action = :action "
                "ORDER BY sequence"
            ),
            {"entity_id": str(entity_id), "action": action},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_first_login_emits_a_user_identity_created_event(app_db: Connection) -> None:
    session = Session(bind=app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="sub-created-1",
            preferred_username="alice",
            display_name="Alice",
            email="alice@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    assert result.user is not None

    identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": result.user.id}
    ).scalar_one()

    events = _events_for_action(session, identity_id, "user_identity.created")
    assert len(events) == 1
    event = events[0]
    assert event["entity_type"] == "user_identity"
    assert event["before"] is None
    assert event["after"]["email_verified"] is True
    assert event["after"]["_redacted"] == ["email", "issuer", "subject"]
    assert set(event["after"]) == {"email_verified", "_redacted"}


@pytest.mark.req("NFR-26")
@pytest.mark.integration
def test_user_identity_created_event_never_carries_the_withheld_values(
    app_db: Connection,
) -> None:
    """AC-4 in the database, for `user_identity` specifically: the
    withheld fields (issuer, subject, email) are named under `_redacted`
    only - their actual values must appear nowhere in the row."""
    session = Session(bind=app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://distinctive-issuer.example",
            subject="distinctive-subject-value",
            preferred_username="bea",
            display_name="Bea",
            email="distinctive-email@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    assert result.user is not None

    identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": result.user.id}
    ).scalar_one()

    events = _events_for_action(session, identity_id, "user_identity.created")
    assert len(events) == 1
    full_row_text = str(events[0])
    assert "distinctive-issuer" not in full_row_text
    assert "distinctive-subject-value" not in full_row_text
    assert "distinctive-email" not in full_row_text


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_auto_linked_login_emits_a_user_identity_created_event_for_the_new_identity(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)
    created = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="sub-auto-first",
            preferred_username="carol",
            display_name="Carol",
            email="shared-auto@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    assert created.user is not None

    linked = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_B,
            subject="sub-auto-second",
            email="shared-auto@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    assert linked.user is not None
    assert linked.user.id == created.user.id

    linked_identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id AND issuer = :issuer"),
        {"id": linked.user.id, "issuer": _TRUSTED_B},
    ).scalar_one()

    events = _events_for_action(session, linked_identity_id, "user_identity.created")
    assert len(events) == 1


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_repeat_login_that_changes_nothing_emits_no_refresh_event(app_db: Connection) -> None:
    session = Session(bind=app_db)
    claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-repeat-unchanged",
        preferred_username="dave",
        display_name="Dave",
        email="dave@example.com",
        email_verified=True,
    )

    first = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert first.user is not None

    identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": first.user.id}
    ).scalar_one()

    second = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert second.user is not None

    events = _events_for_action(session, identity_id, "user_identity.refreshed")
    assert events == []


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_repeat_login_with_a_changed_email_verified_claim_emits_one_refresh_event(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)
    first_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-repeat-changed",
        preferred_username="erin",
        display_name="Erin",
        email="erin@example.com",
        email_verified=False,
    )
    second_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-repeat-changed",
        preferred_username="erin",
        display_name="Erin",
        email="erin@example.com",
        email_verified=True,
    )

    first = resolve_user_for_claims(
        session, first_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert first.user is not None

    identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": first.user.id}
    ).scalar_one()

    second = resolve_user_for_claims(
        session, second_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert second.user is not None

    events = _events_for_action(session, identity_id, "user_identity.refreshed")
    assert len(events) == 1
    event = events[0]
    assert event["before"] == {"email_verified": False}
    assert event["after"] == {"email_verified": True}


@pytest.mark.req("NFR-08")
@pytest.mark.req("NFR-26")
@pytest.mark.integration
def test_a_repeat_login_with_only_a_changed_email_emits_a_withheld_only_refresh_event(
    app_db: Connection,
) -> None:
    """The commoner real case, and a distinct payload shape from the
    `email_verified`-flip test above: `email` is withheld, so a change to
    it alone produces a diff that is non-empty *solely* through
    `_redacted` - `before`/`after` are each `{"_redacted": ["email"]}`,
    with no `changes` entries at all. This is the shape closest to
    `AuditNoOpError` if `email` were ever reclassified as ignored rather
    than withheld, so it deserves its own assertion rather than riding
    along with the `email_verified` case."""
    session = Session(bind=app_db)
    first_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-repeat-email-only",
        preferred_username="holly",
        display_name="Holly",
        email="holly-old@example.com",
        email_verified=True,
    )
    second_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-repeat-email-only",
        preferred_username="holly",
        display_name="Holly",
        email="holly-new@example.com",
        email_verified=True,
    )

    first = resolve_user_for_claims(
        session, first_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert first.user is not None

    identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": first.user.id}
    ).scalar_one()

    second = resolve_user_for_claims(
        session, second_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert second.user is not None

    events = _events_for_action(session, identity_id, "user_identity.refreshed")
    assert len(events) == 1
    event = events[0]
    assert event["before"] == {"_redacted": ["email"]}
    assert event["after"] == {"_redacted": ["email"]}
    full_row_text = str(event)
    assert "holly-old" not in full_row_text
    assert "holly-new" not in full_row_text


def _create_active_user_with_identities(
    session: Session, username: str, count: int
) -> tuple[User, list[uuid.UUID]]:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    session.add(user)
    session.flush()
    identity_ids: list[uuid.UUID] = []
    for i in range(count):
        identity = UserIdentity(
            user_id=user.id,
            issuer=f"https://idp-{i}.example",
            subject=f"sub-{username}-{i}",
            email=f"{username}{i}@example.com",
            email_verified=True,
        )
        session.add(identity)
        session.flush()
        identity_ids.append(identity.id)
    return user, identity_ids


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_close_account_emits_one_user_identity_deleted_event_per_identity(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)
    user, identity_ids = _create_active_user_with_identities(session, "faith", 2)

    close_account(session, user.id, AuditContext.system())
    session.flush()

    for identity_id in identity_ids:
        events = _events_for_action(session, identity_id, "user_identity.deleted")
        assert len(events) == 1
        event = events[0]
        assert event["entity_type"] == "user_identity"
        assert event["after"] is None
        assert event["before"]["email_verified"] is True
        assert event["before"]["_redacted"] == ["email", "issuer", "subject"]


@pytest.mark.req("NFR-26")
@pytest.mark.integration
def test_user_identity_deleted_event_never_carries_the_withheld_values(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)
    user = User(username="gary", display_name="Gary", organisation="RCPA-QAP")
    session.add(user)
    session.flush()
    identity = UserIdentity(
        user_id=user.id,
        issuer="https://gary-issuer.example",
        subject="gary-subject-value",
        email="gary-email@example.com",
        email_verified=True,
    )
    session.add(identity)
    session.flush()
    identity_id = identity.id

    close_account(session, user.id, AuditContext.system())
    session.flush()

    events = _events_for_action(session, identity_id, "user_identity.deleted")
    assert len(events) == 1
    full_row_text = str(events[0])
    assert "gary-issuer" not in full_row_text
    assert "gary-subject-value" not in full_row_text
    assert "gary-email" not in full_row_text
