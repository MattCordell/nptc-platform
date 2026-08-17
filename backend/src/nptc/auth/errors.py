"""The NFR-07 token-verification error hierarchy (issue #43).

One base, mirroring ``nptc_shared.terminology.errors``'s convention, so a
future FastAPI dependency (#41/#142/#143) can catch a single type and map
it to a 401 response without a long except-chain.

Every one of these is a refusal, never a bypass: there is no code path
anywhere in ``nptc.auth`` in which failing to establish one of these
conditions results in a token being treated as valid.
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
    """No signing key could be obtained for the token's ``kid`` - the
    JWKS endpoint is unreachable and no cached key matches. A rejection,
    never a bypass: there is no path from "keys unavailable" to
    "accept"."""
