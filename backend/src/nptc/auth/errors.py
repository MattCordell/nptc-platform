"""The NFR-07 token-verification error hierarchy.

One base, mirroring ``nptc_shared.terminology.errors``, so the API layer catches
a single type and maps it to a 401 (``nptc.api.errors``).

Every member is a refusal, never a bypass: no code path in ``nptc.auth`` treats
a token as valid after failing to establish one of these conditions.
"""

from __future__ import annotations


class TokenError(Exception):
    """Base for every reason ``nptc.auth.tokens.TokenVerifier.verify`` (or
    the ``SigningKeys`` it calls) refuses a token."""


class TokenInvalidError(TokenError):
    """The token is malformed, its signature does not verify, or its
    header names a disallowed ``alg``/``typ`` - includes ``alg: none`` and
    an RS256-signed token replayed as HS256 against the RSA public key."""


class TokenExpiredError(TokenError):
    """The token's ``exp`` claim is in the past."""


class TokenIssuerError(TokenError):
    """The token's ``iss`` claim does not match the configured issuer."""


class TokenAudienceError(TokenError):
    """The token's ``aud`` claim does not include the configured
    audience."""


class TokenClaimsError(TokenError):
    """A required claim is missing, or a claim this module depends on
    (``sub``) is present but blank."""


class SigningKeyUnavailableError(TokenError):
    """No signing key could be obtained for the token's ``kid``: the JWKS
    endpoint is unreachable and no cached key matches."""
