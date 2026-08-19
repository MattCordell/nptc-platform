"""NFR-06 end to end: `principal_for`'s structural suppression of the
Administrator role feeding `require_permission`'s `MfaRequiredError` path
(issue #44). `test_principal_derivation.py` covers `Principal` derivation
in isolation and `test_authorisation_checks.py` covers `require_permission`
in isolation; this file is the seam between the two - the actual
end-to-end behaviour a future #41 dependency chain relies on.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.authorisation import require_permission
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.identity import LinkOutcome, Resolution, resolve_user_for_claims
from nptc.auth.permissions import Permission, Role
from nptc.auth.principal import principal_for

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


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_admin_only_permission_raises_mfa_required_without_step_up(app_db: Connection) -> None:
    session = Session(bind=app_db)
    resolution = resolve_user_for_claims(
        session,
        _claims(subject="sub-mfa-e2e-1"),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )
    session.flush()
    assert resolution.user is not None
    grant_role_unchecked(
        session,
        target_user_id=resolution.user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    principal = principal_for(
        session,
        Resolution(outcome=LinkOutcome.EXISTING, user=resolution.user),
        claims=_claims(subject="sub-mfa-e2e-1", acr=None),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    with pytest.raises(MfaRequiredError):
        require_permission(Permission.RELEASE_PUBLISH)(principal)


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_admin_only_permission_succeeds_after_step_up(app_db: Connection) -> None:
    session = Session(bind=app_db)
    resolution = resolve_user_for_claims(
        session,
        _claims(subject="sub-mfa-e2e-2"),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )
    session.flush()
    assert resolution.user is not None
    grant_role_unchecked(
        session,
        target_user_id=resolution.user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    principal = principal_for(
        session,
        Resolution(outcome=LinkOutcome.EXISTING, user=resolution.user),
        claims=_claims(subject="sub-mfa-e2e-2", acr="2"),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    assert require_permission(Permission.RELEASE_PUBLISH)(principal) is principal


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_a_principal_who_never_held_administrator_gets_a_plain_denial(app_db: Connection) -> None:
    """A principal who was never granted Administrator at all has nothing
    in `mfa_suppressed_roles` (there is no suppressed grant to blame the
    denial on), so refusing an admin-only permission for them must stay a
    plain `PermissionDeniedError`, never the actionable `MfaRequiredError`
    - that error is reserved for "you could do this if you stepped up",
    not "you were never going to be allowed to do this"."""
    session = Session(bind=app_db)
    resolution = resolve_user_for_claims(
        session,
        _claims(subject="sub-mfa-e2e-3"),
        trusted_issuers=frozenset(),
        audit=AuditContext.system(),
    )
    session.flush()
    assert resolution.user is not None

    principal = principal_for(
        session,
        Resolution(outcome=LinkOutcome.EXISTING, user=resolution.user),
        claims=_claims(subject="sub-mfa-e2e-3", acr=None),
        mfa_acr_values=_MFA_ACR_VALUES,
    )

    with pytest.raises(PermissionDeniedError) as excinfo:
        require_permission(Permission.RELEASE_PUBLISH)(principal)
    assert not isinstance(excinfo.value, MfaRequiredError)
