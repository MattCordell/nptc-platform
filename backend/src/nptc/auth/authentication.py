"""The join between NFR-07 token verification and #42's identity
resolution (issue #43).

A token proves who the user is; the database decides what they may do
(NFR-07's second sentence, NFR-20). This module is the seam: it never
returns a ``Resolution`` for a token whose signature, issuer, audience and
expiry were not all checked first.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.identity import Resolution, resolve_user_for_claims
from nptc.auth.tokens import TokenVerifier


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """The verified claims *and* the account they resolved to.

    Both halves exist because `nptc.auth.principal.principal_for` needs
    the claims (for `acr`, the NFR-06 MFA fact) as well as the
    `Resolution`. Returning them together means the caller verifies the
    token exactly once - a second `verify()` call to recover the claims
    would repeat a signature check on every single request, and would
    open the (small, but real) window in which the two verifications
    could disagree, e.g. across an `exp` boundary.
    """

    claims: OidcIdentityClaims
    resolution: Resolution


def authenticate_identity(
    session: Session,
    token: str,
    *,
    verifier: TokenVerifier,
    trusted_issuers: frozenset[str],
    audit: AuditContext,
) -> AuthenticatedIdentity:
    """Verifies ``token``, then resolves it to an internal ``app_user``,
    returning both halves.

    Raises a ``nptc.auth.errors.TokenError`` for any verification failure -
    there is no code path that reaches ``resolve_user_for_claims`` with an
    unverified claim.
    """
    claims = verifier.verify(token)
    resolution = resolve_user_for_claims(
        session, claims, trusted_issuers=trusted_issuers, audit=audit
    )
    return AuthenticatedIdentity(claims=claims, resolution=resolution)


def authenticate(
    session: Session,
    token: str,
    *,
    verifier: TokenVerifier,
    trusted_issuers: frozenset[str],
    audit: AuditContext,
) -> Resolution:
    """The `Resolution`-only form of ``authenticate_identity``, kept for
    callers that have no use for the claims."""
    return authenticate_identity(
        session,
        token,
        verifier=verifier,
        trusted_issuers=trusted_issuers,
        audit=audit,
    ).resolution
