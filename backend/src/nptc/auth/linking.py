"""The NFR-05 auto-link predicate.

Pure (no DB, no I/O), so it is unit-testable without Docker.

Auto-linking on an unverified email lets anyone who can mint a token asserting
an administrator's email inherit that administrator's privileges. Two rules
follow:

- Issuer membership is **exact set membership**, never ``startswith`` or a
  substring match, which would let ``https://good.example.attacker.com`` pass
  as ``https://good.example``.
- ``email_verified`` is checked with ``is True``, never truthiness: a claim
  decoded as the string ``"false"`` is truthy in Python.
"""

from __future__ import annotations

from nptc.auth.claims import OidcIdentityClaims


def may_auto_link(claims: OidcIdentityClaims, trusted_issuers: frozenset[str]) -> bool:
    return claims.issuer in trusted_issuers and claims.email_verified is True and bool(claims.email)
