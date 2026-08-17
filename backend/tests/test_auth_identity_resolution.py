"""`resolve_user_for_claims` tests (issue #42, NFR-04/NFR-05/NFR-17).

`Session(bind=app_db)` joins the existing testcontainers fixture
connection (the standard join-an-external-transaction recipe) rather than
building a separate engine/sessionmaker - the fixture's per-test rollback
untouched, and `identity.py` itself never calls `session.commit()`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.identity import LinkOutcome, close_account, resolve_user_for_claims

_TRUSTED = frozenset({"https://good.example"})
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


def _identity_count(app_db: Connection) -> int:
    return app_db.execute(text("SELECT count(*) FROM user_identity")).scalar_one()


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_same_subject_twice_resolves_to_one_user(app_db: Connection) -> None:
    session = Session(bind=app_db)
    claims = _claims(
        issuer=_UNTRUSTED, subject="sub-1", preferred_username="alice", display_name="Alice"
    )

    first = resolve_user_for_claims(session, claims, trusted_issuers=_TRUSTED)
    second = resolve_user_for_claims(session, claims, trusted_issuers=_TRUSTED)

    assert first.outcome == LinkOutcome.CREATED
    assert second.outcome == LinkOutcome.EXISTING
    assert first.user is not None and second.user is not None
    assert first.user.id == second.user.id


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_same_subject_with_changed_email_and_display_name_still_resolves_to_the_same_user(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)
    first_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-2",
        preferred_username="bob",
        display_name="Bob",
        email="bob@old.example",
        email_verified=False,
    )
    changed_claims = _claims(
        issuer=_UNTRUSTED,
        subject="sub-2",
        preferred_username="bob",
        display_name="Bob Two",
        email="bob@new.example",
        email_verified=True,
    )

    created = resolve_user_for_claims(session, first_claims, trusted_issuers=_TRUSTED)
    updated = resolve_user_for_claims(session, changed_claims, trusted_issuers=_TRUSTED)

    assert created.user is not None and updated.user is not None
    assert created.user.id == updated.user.id
    assert updated.outcome == LinkOutcome.EXISTING
    assert updated.user.display_name == "Bob Two"


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_different_subject_same_issuer_creates_a_distinct_user(app_db: Connection) -> None:
    session = Session(bind=app_db)
    first = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED, subject="sub-3a", preferred_username="carol", display_name="Carol"
        ),
        trusted_issuers=_TRUSTED,
    )
    second = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED, subject="sub-3b", preferred_username="dave", display_name="Dave"
        ),
        trusted_issuers=_TRUSTED,
    )

    assert first.user is not None and second.user is not None
    assert first.user.id != second.user.id


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_same_subject_from_a_different_issuer_creates_a_distinct_user(app_db: Connection) -> None:
    """The principal failure mode this table exists to prevent: keying on
    `sub` alone, ignoring `iss`, would collapse two different IdPs'
    identically-named subjects into one account."""
    session = Session(bind=app_db)
    first = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://idp-a.example",
            subject="shared-subject",
            preferred_username="erin",
            display_name="Erin",
        ),
        trusted_issuers=_TRUSTED,
    )
    second = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://idp-b.example",
            subject="shared-subject",
            preferred_username="frank",
            display_name="Frank",
        ),
        trusted_issuers=_TRUSTED,
    )

    assert first.user is not None and second.user is not None
    assert first.user.id != second.user.id


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_no_user_column_ever_holds_the_oidc_subject(app_db: Connection) -> None:
    session = Session(bind=app_db)
    subject = "distinctive-subject-value-xyz"
    resolve_user_for_claims(
        session,
        _claims(issuer=_UNTRUSTED, subject=subject, preferred_username="kim", display_name="Kim"),
        trusted_issuers=_TRUSTED,
    )
    session.flush()

    rows = app_db.execute(text("SELECT * FROM app_user")).mappings().all()
    for row in rows:
        for value in row.values():
            assert subject not in str(value)


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_verified_email_from_a_trusted_issuer_auto_links(app_db: Connection) -> None:
    session = Session(bind=app_db)
    created = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="sub-first",
            preferred_username="gina",
            display_name="Gina",
            email="shared@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
    )

    linked = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://good.example",
            subject="sub-second",
            email="shared@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
    )

    assert created.user is not None and linked.user is not None
    assert linked.outcome == LinkOutcome.AUTO_LINKED
    assert linked.user.id == created.user.id


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_unverified_email_requires_a_manual_link_and_writes_nothing(app_db: Connection) -> None:
    session = Session(bind=app_db)
    resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="sub-third",
            preferred_username="henry",
            display_name="Henry",
            email="shared2@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
    )
    session.flush()
    before = _identity_count(app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://good.example",
            subject="sub-fourth",
            email="shared2@example.com",
            email_verified=False,
        ),
        trusted_issuers=_TRUSTED,
    )
    session.flush()

    assert result.outcome == LinkOutcome.MANUAL_LINK_REQUIRED
    assert result.user is None
    assert _identity_count(app_db) == before


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_untrusted_issuer_does_not_auto_link_even_with_a_verified_email(app_db: Connection) -> None:
    session = Session(bind=app_db)
    resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="sub-fifth",
            preferred_username="iris",
            display_name="Iris",
            email="shared3@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
    )
    session.flush()
    before = _identity_count(app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://another-untrusted.example",
            subject="sub-sixth",
            email="shared3@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
    )
    session.flush()

    assert result.outcome == LinkOutcome.MANUAL_LINK_REQUIRED
    assert result.user is None
    assert _identity_count(app_db) == before


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_closed_account_subject_does_not_resolve_to_the_tombstoned_user(app_db: Connection) -> None:
    """Documented consequence of closure: the identity row is deleted, so
    the same subject logging in again yields a brand new user, not the
    tombstoned one (AC's "can no longer authenticate into the tombstoned
    user")."""
    session = Session(bind=app_db)
    claims = _claims(
        issuer=_UNTRUSTED, subject="sub-seventh", preferred_username="jack", display_name="Jack"
    )
    original = resolve_user_for_claims(session, claims, trusted_issuers=_TRUSTED)
    session.flush()
    assert original.user is not None
    original_id = original.user.id

    close_account(session, original_id)
    session.flush()

    again = resolve_user_for_claims(session, claims, trusted_issuers=_TRUSTED)

    assert again.outcome == LinkOutcome.CREATED
    assert again.user is not None
    assert again.user.id != original_id
