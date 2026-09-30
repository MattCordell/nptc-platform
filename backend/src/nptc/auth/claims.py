"""The OIDC identity claim shape.

`nptc.auth.tokens` produces one of these from a verified token and
`nptc.auth.identity` consumes it to resolve or link an `app_user`. Nothing here
parses a JWT or talks to Keycloak.

**`acr` and `auth_time` (NFR-06) are authentication facts, not authorisation
claims.** They say *how* and *when* the user authenticated, never *what they may
do*, which stays with the platform database (NFR-07). Do not read them as
licence to add `realm_access`, `resource_access` or `groups`: those are
authorisation-shaped and belong nowhere near a JWT claim (ADR-0014,
`test_token_verification_guard.py`'s rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OidcIdentityClaims:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    preferred_username: str | None
    display_name: str | None
    #: The OIDC Authentication Context Class Reference, which Keycloak's
    #: step-up flow stamps when a login satisfies a configured level.
    #: `principal_for` checks it against `AuthSettings.mfa_acr_values`. `None`
    #: for an ordinary login that requested no `acr_values`.
    acr: str | None = None
    #: The Unix timestamp of the authentication event. Recorded only: no
    #: maximum age is enforced.
    auth_time: int | None = None
