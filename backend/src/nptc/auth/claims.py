"""The OIDC identity claim shape (issue #42).

The seam between #43 and #42: #43 produces one of these from a verified
token, #42 consumes it to resolve or link an internal `app_user`. Nothing
here parses a JWT or talks to Keycloak - this module has no I/O at all.

**`acr`/`auth_time` (issue #44, NFR-06) are authentication facts, not
authorisation claims** - they say *how* and *when* the user authenticated,
never *what they may do* (which stays exclusively the platform database's
job, per NFR-07's second sentence). That distinction is what allows them
here without contradicting the "no roles/groups/scopes on this type"
posture the rest of this docstring would otherwise imply: don't read this
as licence to add `realm_access`/`resource_access`/`groups` - those *are*
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
    #: The OIDC Authentication Context Class Reference - Keycloak's step-up
    #: mechanism stamps this when a login satisfies a configured level of
    #: authentication (see `nptc.auth.principal.principal_for`'s NFR-06
    #: check against `AuthSettings.mfa_acr_values`). `None` when absent -
    #: an ordinary login without a requested `acr_values` parameter.
    #: Defaulted (not required) so every pre-#44 call site constructing
    #: this type - test fixtures included - keeps working unchanged.
    acr: str | None = None
    #: The Unix timestamp of the authentication event (`auth_time`) -
    #: recorded now, enforcement of a maximum age deferred past this issue
    #: (see docs/adr/0019-permission-framework.md's Consequences).
    auth_time: int | None = None
