"""The NFR-05 auto-link predicate (issue #42).

Pure - no DB, no I/O - so it can be unit tested without Docker and reused
identically by both the resolution flow and any future admin-facing "would
this auto-link?" check.

The PRD names the failure mode this predicate exists to prevent bluntly:
auto-linking on an unverified email claim means anyone who can mint a
token asserting an administrator's email inherits that administrator's
privileges. Two things follow from that:

- Issuer membership is checked by **exact set membership**, never
  ``startswith``/substring - a prefix or substring match lets
  ``https://good.example.attacker.com`` or a query-string trick pass as
  ``https://good.example``.
- ``email_verified`` is checked with ``is True``, never truthiness - a
  claim decoded as the string ``"false"`` is truthy in Python.
"""

from __future__ import annotations

from nptc.auth.claims import OidcIdentityClaims


def may_auto_link(claims: OidcIdentityClaims, trusted_issuers: frozenset[str]) -> bool:
    return claims.issuer in trusted_issuers and claims.email_verified is True and bool(claims.email)
