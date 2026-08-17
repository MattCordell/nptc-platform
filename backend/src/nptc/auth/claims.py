"""The OIDC identity claim shape (issue #42).

The seam between #43 and #42: #43 produces one of these from a verified
token, #42 consumes it to resolve or link an internal `app_user`. Nothing
here parses a JWT or talks to Keycloak - this module has no I/O at all.
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
