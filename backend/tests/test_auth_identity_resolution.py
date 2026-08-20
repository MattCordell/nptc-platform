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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.identity import LinkOutcome, close_account, resolve_user_for_claims
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


def _identity_count(app_db: Connection) -> int:
    return app_db.execute(text("SELECT count(*) FROM user_identity")).scalar_one()


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_same_subject_twice_resolves_to_one_user(app_db: Connection) -> None:
    session = Session(bind=app_db)
    claims = _claims(
        issuer=_UNTRUSTED, subject="sub-1", preferred_username="alice", display_name="Alice"
    )

    first = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    second = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )

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

    created = resolve_user_for_claims(
        session, first_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    updated = resolve_user_for_claims(
        session, changed_claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )

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
        audit=AuditContext.system(),
    )
    second = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED, subject="sub-3b", preferred_username="dave", display_name="Dave"
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
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
        audit=AuditContext.system(),
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
        audit=AuditContext.system(),
    )

    assert first.user is not None and second.user is not None
    assert first.user.id != second.user.id


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_no_user_column_ever_holds_the_oidc_subject(app_db: Connection) -> None:
    session = Session(bind=app_db)
    subject = "distinctive-subject-value-xyz"
    result = resolve_user_for_claims(
        session,
        _claims(issuer=_UNTRUSTED, subject=subject, preferred_username="kim", display_name="Kim"),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()

    # Scoped to the row this test created, not every row `app_user` has
    # ever committed - a whole-table scan would also depend on nothing
    # else in the shared container ever having committed this subject
    # (issue #190).
    assert result.user is not None
    row = (
        app_db.execute(text("SELECT * FROM app_user WHERE id = :id"), {"id": result.user.id})
        .mappings()
        .one()
    )
    for value in row.values():
        assert subject not in str(value)


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_verified_email_from_a_trusted_issuer_auto_links(app_db: Connection) -> None:
    """Both ends of the link must be trusted: the *seeding* identity
    (created via `_TRUSTED_A`) and the incoming claim (via `_TRUSTED_B`)."""
    session = Session(bind=app_db)
    created = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="sub-first",
            preferred_username="gina",
            display_name="Gina",
            email="shared@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )

    linked = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_B,
            subject="sub-second",
            email="shared@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
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
            issuer=_TRUSTED_A,
            subject="sub-third",
            preferred_username="henry",
            display_name="Henry",
            email="shared2@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    before = _identity_count(app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_B,
            subject="sub-fourth",
            email="shared2@example.com",
            email_verified=False,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()

    assert result.outcome == LinkOutcome.MANUAL_LINK_REQUIRED
    assert result.user is None
    assert _identity_count(app_db) == before


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_an_untrusted_issuers_own_verified_email_never_becomes_an_auto_link_target(
    app_db: Connection,
) -> None:
    """Regression for the exact bypass this table exists to close: a first
    registration through *any* issuer at all (including one nobody
    trusts) must not be able to plant a verified email that a later,
    genuinely trusted login then auto-links into. Because the seeding
    identity's own issuer is untrusted, it is never a candidate at all -
    the second login creates its own, entirely independent account rather
    than being linked (or even flagged for manual link) against the
    first."""
    session = Session(bind=app_db)
    planted = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="attacker-sub",
            preferred_username="iris",
            display_name="Iris",
            email="admin@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()
    assert planted.outcome == LinkOutcome.CREATED
    assert planted.user is not None

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="real-admin-sub",
            email="admin@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()

    assert result.outcome == LinkOutcome.CREATED
    assert result.user is not None
    assert result.user.id != planted.user.id


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_multiple_candidate_users_with_the_same_verified_email_requires_manual_link(
    app_db: Connection,
) -> None:
    """Ambiguous, not guessed: if more than one existing user has a
    trusted, verified identity for the same email, an incoming claim that
    would otherwise auto-link must not pick one via undefined query-plan
    order - it needs a human. Both candidates are seeded directly (not via
    `resolve_user_for_claims`, which would itself auto-link the second
    seed into the first the moment two matching, trusted, verified
    identities exist) - the ambiguity has to already exist before this
    function is ever asked to resolve anything against it."""
    session = Session(bind=app_db)
    first_user = User(username="ambiguous1", display_name="Ambiguous One")
    second_user = User(username="ambiguous2", display_name="Ambiguous Two")
    session.add_all([first_user, second_user])
    session.flush()
    session.add_all(
        [
            UserIdentity(
                user_id=first_user.id,
                issuer=_TRUSTED_A,
                subject="sub-cand-1",
                email="dupe@example.com",
                email_verified=True,
            ),
            UserIdentity(
                user_id=second_user.id,
                issuer=_TRUSTED_B,
                subject="sub-cand-2",
                email="dupe@example.com",
                email_verified=True,
            ),
        ]
    )
    session.flush()
    before = _identity_count(app_db)

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="sub-cand-3",
            email="dupe@example.com",
            email_verified=True,
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()

    assert result.outcome == LinkOutcome.MANUAL_LINK_REQUIRED
    assert result.user is None
    assert _identity_count(app_db) == before


@pytest.mark.req("NFR-05")
@pytest.mark.integration
def test_empty_trusted_issuer_set_prevents_auto_linking_end_to_end(app_db: Connection) -> None:
    """`test_empty_trusted_issuer_set_never_auto_links` in
    test_auth_linking_policy.py covers `may_auto_link` in isolation; this
    exercises the same fail-closed default through the full resolution
    path, including `_find_candidate_user_ids`'s own separate
    `not trusted_issuers` short-circuit - with no trusted issuer at all,
    even a same-issuer, verified-email rematch must not resolve to the
    seeded account; it creates its own, independent one instead."""
    session = Session(bind=app_db)
    seeded = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="sub-e2e-seed",
            preferred_username="e2e-seed",
            display_name="Seed",
            email="e2e@example.com",
            email_verified=True,
        ),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )
    session.flush()
    assert seeded.user is not None

    result = resolve_user_for_claims(
        session,
        _claims(
            issuer=_TRUSTED_A,
            subject="sub-e2e-second",
            email="e2e@example.com",
            email_verified=True,
        ),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )

    assert result.outcome == LinkOutcome.CREATED
    assert result.user is not None
    assert result.user.id != seeded.user.id


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_first_registration_with_no_username_or_display_name_claim_still_succeeds(
    app_db: Connection,
) -> None:
    """Real IdPs routinely omit `preferred_username`/`display_name` -
    `app_user`'s tombstone CHECK requires both to be non-null for a
    non-closed row, so first registration must fall back rather than
    surface a raw IntegrityError on ordinary input."""
    session = Session(bind=app_db)

    result = resolve_user_for_claims(
        session,
        _claims(issuer=_UNTRUSTED, subject="sub-no-profile"),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )

    assert result.outcome == LinkOutcome.CREATED
    assert result.user is not None
    assert result.user.username
    assert result.user.display_name


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_first_registration_never_derives_the_username_from_the_email_address(
    app_db: Connection,
) -> None:
    """`username` is one of the fields `UserRef` exposes externally - a
    user who never chose a handle must not have one silently minted from
    the local-part of an email address supplied only for verification."""
    session = Session(bind=app_db)

    result = resolve_user_for_claims(
        session,
        _claims(issuer=_UNTRUSTED, subject="sub-email-only", email="j.smith@example.com"),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )

    assert result.user is not None
    assert result.user.username is not None
    assert not result.user.username.startswith("j.smith")


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_first_registration_treats_a_whitespace_only_username_claim_as_missing(
    app_db: Connection,
) -> None:
    session = Session(bind=app_db)

    result = resolve_user_for_claims(
        session,
        _claims(issuer=_UNTRUSTED, subject="sub-blank-username", preferred_username="   "),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )

    assert result.user is not None
    assert result.user.username is not None
    assert result.user.username.strip() == result.user.username
    assert result.user.username != "   "


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_first_registration_falls_back_to_a_new_username_on_a_collision(
    app_db: Connection,
) -> None:
    """A `preferred_username` claim already held by another user must not
    abort first login with a raw 23505 - it must fall back to a distinct
    username instead. Also pins `_create_user`'s claim that a rolled-back
    collision retry never leaves an audit record of an insert that did not
    happen: `second`'s first attempt collides and its whole SAVEPOINT
    (including the `user_identity.created` event emitted inside it) rolls
    back, so exactly one such event survives for `second`'s identity, not
    two."""
    session = Session(bind=app_db)
    first = resolve_user_for_claims(
        session,
        _claims(
            issuer=_UNTRUSTED,
            subject="sub-taken-1",
            preferred_username="taken",
            display_name="First",
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )
    session.flush()

    second = resolve_user_for_claims(
        session,
        _claims(
            issuer="https://idp-c.example",
            subject="sub-taken-2",
            preferred_username="taken",
            display_name="Second",
        ),
        trusted_issuers=_TRUSTED,
        audit=AuditContext.system(),
    )

    assert first.user is not None and second.user is not None
    assert first.user.id != second.user.id
    assert second.user.username != first.user.username
    assert second.user.username is not None and second.user.username.startswith("taken")

    second_identity_id = session.execute(
        text("SELECT id FROM user_identity WHERE user_id = :id"), {"id": second.user.id}
    ).scalar_one()
    created_events = session.execute(
        text(
            "SELECT count(*) FROM audit_event "
            "WHERE entity_id = :entity_id AND action = 'user_identity.created'"
        ),
        {"entity_id": str(second_identity_id)},
    ).scalar_one()
    assert created_events == 1


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_first_registration_reraises_a_non_username_constraint_violation(
    app_db: Connection,
) -> None:
    """The behaviour `_is_username_collision` exists to fix: only a
    `uq_app_user_username` collision (23505) is retried. A blank `subject`
    violates `ck_user_identity_subject_not_blank` (23514) instead - a
    different sqlstate and constraint the retry loop must not mistake for
    a username problem - and this must surface as-is rather than burn
    every retry attempt and then report a misleading "could not allocate
    a unique username" error. This is also the one test that actually
    exercises the `orig.diag.constraint_name` access against a real driver
    exception, rather than trusting the getattr fallback never fires."""
    session = Session(bind=app_db)

    with pytest.raises(IntegrityError):
        resolve_user_for_claims(
            session,
            _claims(issuer=_UNTRUSTED, subject="   "),
            trusted_issuers=_TRUSTED,
            audit=AuditContext.system(),
        )


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
    original = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )
    session.flush()
    assert original.user is not None
    original_id = original.user.id

    close_account(session, original_id, AuditContext.system())
    session.flush()

    again = resolve_user_for_claims(
        session, claims, trusted_issuers=_TRUSTED, audit=AuditContext.system()
    )

    assert again.outcome == LinkOutcome.CREATED
    assert again.user is not None
    assert again.user.id != original_id
