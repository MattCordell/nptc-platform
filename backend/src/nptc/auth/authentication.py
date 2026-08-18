"""The join between NFR-07 token verification and #42's identity
resolution (issue #43).

A token proves who the user is; the database decides what they may do
(NFR-07's second sentence, NFR-20). This module is the seam: it never
returns a ``Resolution`` for a token whose signature, issuer, audience and
expiry were not all checked first.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.identity import Resolution, resolve_user_for_claims
from nptc.auth.tokens import TokenVerifier


def authenticate(
    session: Session,
    token: str,
    *,
    verifier: TokenVerifier,
    trusted_issuers: frozenset[str],
    audit: AuditContext,
) -> Resolution:
    """Verifies ``token``, then resolves it to an internal ``app_user``.

    Raises a ``nptc.auth.errors.TokenError`` for any verification failure -
    there is no code path that reaches ``resolve_user_for_claims`` with an
    unverified claim.
    """
    claims = verifier.verify(token)
    return resolve_user_for_claims(session, claims, trusted_issuers=trusted_issuers, audit=audit)
