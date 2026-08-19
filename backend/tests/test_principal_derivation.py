"""`nptc.auth.principal.principal_for` (issue #44, NFR-20, NFR-06): turning
a `Resolution` plus verified claims into the `Principal` an authorisation
check inspects. Real Postgres via testcontainers - `principal_for` reads
`user_role` rows through the session, so a fake/in-memory session would
not exercise the query it actually runs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.errors_authorisation import AccountClosedError, ManualLinkRequiredError
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.identity import LinkOutcome, Resolution, resolve_user_for_claims
from nptc.auth.permissions import ROLE_PERMISSIONS, Permission, Role
from nptc.auth.principal import principal_for
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_role import UserRole

_MFA_ACR_VALUES = frozenset({"2"})


def _claims(*, subject: str, acr: str | None = None) -> OidcIdentityClaims:
    return OidcIdentityClaims(
        issuer="https://idp.example",
        subject=subject,
        email=None,
        email_verified=False,
        preferred_username=subject,
        display_name=subject,
        acr=acr,
    )


def _create_user(session: Session, *, subject: str) -> User:
    result = resolve_user_for_claims(
        session,
        _claims(subject=subject),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )
    session.flush()
    assert result.user is not None
    return result.user


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_a_freshly_registered_user_is_provisional_by_default(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-default-role")

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session,
        resolution,
        claims=_claims(subject="sub-default-role"),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert principal.roles == frozenset({Role.PROVISIONAL})
    assert principal.permissions == ROLE_PERMISSIONS[Role.ANON] | ROLE_PERMISSIONS[Role.PROVISIONAL]


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_a_user_with_zero_grants_is_never_less_capable_than_anonymous(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-zero-grants")
    # Revoke the default Provisional grant to reach the genuinely-zero-
    # grants case directly, without going through nptc.auth.grants.
    session.execute(select(UserRole).where(UserRole.user_id == user.id))
    for grant in (
        session.execute(select(UserRole).where(UserRole.user_id == user.id)).scalars().all()
    ):
        session.delete(grant)
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session,
        resolution,
        claims=_claims(subject="sub-zero-grants"),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert principal.roles == frozenset()
    assert principal.permissions == ROLE_PERMISSIONS[Role.ANON]


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_a_suspended_user_drops_to_the_anonymous_read_surface(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-suspended")
    user.status = UserStatus.SUSPENDED.value
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session, resolution, claims=_claims(subject="sub-suspended"), mfa_acr_values=_MFA_ACR_VALUES
    )

    assert principal.roles == frozenset()
    assert principal.permissions == ROLE_PERMISSIONS[Role.ANON]
    # Not the write-shaped Observer-only rows either - a suspended user is
    # exactly as capable as a stranger, no more.
    assert not principal.has(Permission.SUBMISSION_VIEW)


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_a_closed_user_raises_account_closed_error(app_db: Connection) -> None:
    """Practically unreachable via `resolve_user_for_claims` (closure
    deletes every identity, so a closed user should never again resolve
    to `EXISTING`) - constructed directly here to prove `principal_for`'s
    own fail-closed backstop, independent of that upstream invariant."""
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-closed")
    # The tombstone CHECK (ck_app_user_tombstone/ck_app_user_closed_at)
    # requires every identifying field null and closed_at set together -
    # see User's own docstring on why. nptc.auth.identity.close_account is
    # the real path that does this; here it is done directly so the
    # scenario reaches principal_for without going through
    # resolve_user_for_claims's own closed-user handling.
    user.username = None
    user.display_name = None
    user.organisation = None
    user.status = UserStatus.CLOSED.value
    user.closed_at = datetime.now(UTC)
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    with pytest.raises(AccountClosedError):
        principal_for(
            session,
            resolution,
            claims=_claims(subject="sub-closed"),
            mfa_acr_values=_MFA_ACR_VALUES,
        )


@pytest.mark.req("NFR-20")
def test_manual_link_required_never_degrades_to_anonymous() -> None:
    """No session/database needed - `MANUAL_LINK_REQUIRED` is checked
    before anything is queried."""
    resolution = Resolution(outcome=LinkOutcome.MANUAL_LINK_REQUIRED, user=None)
    with pytest.raises(ManualLinkRequiredError):
        principal_for(
            None,  # type: ignore[arg-type]
            resolution,
            claims=_claims(subject="irrelevant"),
            mfa_acr_values=_MFA_ACR_VALUES,
        )


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_role_is_suppressed_when_acr_does_not_satisfy_mfa(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-admin-no-mfa")
    grant_role_unchecked(
        session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session,
        resolution,
        claims=_claims(subject="sub-admin-no-mfa", acr=None),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert Role.ADMINISTRATOR not in principal.roles
    assert principal.mfa_suppressed_roles == frozenset({Role.ADMINISTRATOR})
    assert not principal.has(Permission.RELEASE_PUBLISH)
    assert principal.mfa_satisfied is False


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_role_is_effective_when_acr_satisfies_mfa(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-admin-with-mfa")
    grant_role_unchecked(
        session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session,
        resolution,
        claims=_claims(subject="sub-admin-with-mfa", acr="2"),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert Role.ADMINISTRATOR in principal.roles
    assert principal.mfa_suppressed_roles == frozenset()
    assert principal.has(Permission.RELEASE_PUBLISH)
    assert principal.mfa_satisfied is True


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_an_acr_value_not_in_the_configured_set_does_not_satisfy_mfa(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_user(session, subject="sub-admin-wrong-acr")
    grant_role_unchecked(
        session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    resolution = Resolution(outcome=LinkOutcome.EXISTING, user=user)
    principal = principal_for(
        session,
        resolution,
        claims=_claims(subject="sub-admin-wrong-acr", acr="1"),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert Role.ADMINISTRATOR not in principal.roles
    assert principal.mfa_satisfied is False
