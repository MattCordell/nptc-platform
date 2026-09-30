"""NFR-07 server-side JWT verification.

``TokenVerifier.verify`` is the one place in this repository that turns a
bearer token into an ``OidcIdentityClaims``; ``nptc.auth.authentication`` joins
it to identity resolution. Every check below runs in the stated order, and
every failure raises a subclass of ``nptc.auth.errors.TokenError``: there is no
path from "verification failed" to "treat as valid".
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import jwt

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.discovery import resolve_jwks_url
from nptc.auth.errors import (
    TokenAudienceError,
    TokenClaimsError,
    TokenExpiredError,
    TokenInvalidError,
    TokenIssuerError,
)
from nptc.auth.jwks import SigningKeys
from nptc.settings import AuthSettings

#: The realm's signature algorithm, stated explicitly: this line kills
#: `alg: none` and an RS256-signed token replayed as HS256 against the RSA
#: public key. `verify` checks it against the header *before* any key lookup,
#: so a disallowed `alg` never causes a JWKS request (ADR-0016).
_ALGORITHMS = ["RS256"]
_REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat"]
#: Keycloak stamps the *payload* claim `typ: Bearer` on access tokens and
#: `typ: ID` on ID tokens; the JOSE *header*'s own `typ` is `JWT` for both
#: (`test_keycloak_jwt.py`). So this is checked against the verified payload,
#: never the unverified header (ADR-0016). A replayed ID token would also fail
#: the audience check, but refusing it by kind names the mistake.
_EXPECTED_TYP = "Bearer"


@dataclass(frozen=True)
class TokenVerifier:
    issuer: str
    audience: str
    keys: SigningKeys
    #: A constructor argument so a deployment with a known clock skew can raise
    #: it deliberately. Defaults to 0.0 because NFR-07 requires an expired token
    #: to be rejected, and Keycloak's `accessTokenLifespan` is 300s (ADR-0016).
    leeway: float = 0.0

    @classmethod
    def from_settings(
        cls, settings: AuthSettings, *, client: httpx.Client | None = None
    ) -> TokenVerifier:
        if not settings.oidc_issuer.strip():
            raise ValueError(
                "AuthSettings.oidc_issuer is empty - a TokenVerifier cannot be "
                "constructed without a trust anchor (fail closed)"
            )

        jwks_url = settings.jwks_url.strip()
        if not jwks_url:
            owns_client = client is None
            http_client = client or httpx.Client()
            try:
                jwks_url = resolve_jwks_url(settings.oidc_issuer, client=http_client)
            finally:
                if owns_client:
                    http_client.close()

        keys = SigningKeys(
            jwks_url,
            cache_seconds=settings.jwks_cache_seconds,
            refresh_cooldown_seconds=settings.jwks_refresh_cooldown_seconds,
        )
        return cls(issuer=settings.oidc_issuer, audience=settings.oidc_audience, keys=keys)

    def verify(self, token: str) -> OidcIdentityClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.DecodeError as exc:
            raise TokenInvalidError(f"malformed token: {exc}") from exc

        alg = header.get("alg")
        if alg not in _ALGORITHMS:
            raise TokenInvalidError(f"alg {alg!r} is not one of {_ALGORITHMS!r}")

        signing_key = self.keys.signing_key_for(token)

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError(str(exc)) from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenIssuerError(str(exc)) from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenAudienceError(str(exc)) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise TokenClaimsError(str(exc)) from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError(str(exc)) from exc

        typ = payload.get("typ")
        if typ != _EXPECTED_TYP:
            raise TokenInvalidError(f"typ {typ!r} is not {_EXPECTED_TYP!r}")

        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.strip():
            raise TokenClaimsError("sub claim is blank")

        issuer = payload["iss"]
        if not isinstance(issuer, str):
            raise TokenClaimsError("iss claim is not a string")

        # jwt.decode returns dict[str, Any]: narrow every other claim to the
        # type OidcIdentityClaims declares, as `sub` is above.
        email = payload.get("email")
        email = email if isinstance(email, str) else None
        preferred_username = payload.get("preferred_username")
        preferred_username = preferred_username if isinstance(preferred_username, str) else None
        display_name = payload.get("name")
        display_name = display_name if isinstance(display_name, str) else None
        # NFR-06: `acr`/`auth_time` are authentication facts, not the
        # authorisation claims ADR-0014 closed the door on (see
        # nptc.auth.claims).
        acr = payload.get("acr")
        acr = acr if isinstance(acr, str) else None
        auth_time = payload.get("auth_time")
        auth_time = auth_time if isinstance(auth_time, int) else None

        return OidcIdentityClaims(
            issuer=issuer,
            subject=subject,
            email=email,
            # `is True`, never truthiness: the string "false" is truthy (see
            # nptc.auth.linking).
            email_verified=payload.get("email_verified") is True,
            preferred_username=preferred_username,
            display_name=display_name,
            acr=acr,
            auth_time=auth_time,
        )
