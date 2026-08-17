"""Signing-key retrieval for NFR-07 token verification (issue #43).

A thin wrapper over ``jwt.PyJWKClient`` adding the two behaviours it does
not itself guarantee:

- **A fallback cache that survives cache expiry.** ``PyJWKClient``'s own
  cache (``lifespan`` seconds) only ever holds the *most recent* fetch. Once
  that lifespan elapses, ``PyJWKClient`` re-fetches on the next lookup and
  *raises* on failure even if it still, logically, has usable key material
  from the previous fetch. This is what makes acceptance criterion 4 true
  independently of PyJWT's own cache lifespan: pinning ``pyjwt[crypto]>=2.13``
  (see backend/pyproject.toml) stops a failed fetch from *wiping* the cache
  (GHSA-fhv5-28vv-h8m8); this module's own ``dict[str, PyJWK]`` is what stops
  an *expired* cache plus a down IdP from rejecting a token whose key this
  process has already seen and validated before.
- **A refresh cooldown.** ``PyJWKClient`` re-fetches the whole JWKS on every
  ``kid`` it doesn't recognise, so an unauthenticated attacker spraying random
  ``kid`` values makes the backend hammer Keycloak. An unknown ``kid`` seen
  within ``refresh_cooldown_seconds`` of the last refresh attempt is refused
  without any HTTP request at all; genuine key rotation still lands within
  one cooldown window.

Never a bypass: there is no path from "no key could be obtained" to
"accept the token anyway" anywhere in this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientError

from nptc.auth.errors import SigningKeyUnavailableError


class SigningKeys:
    def __init__(
        self,
        jwks_url: str,
        *,
        cache_seconds: float = 300.0,
        refresh_cooldown_seconds: float = 30.0,
        timeout_seconds: float = 10.0,
        client: PyJWKClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or PyJWKClient(
            jwks_url, lifespan=cache_seconds, timeout=timeout_seconds
        )
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        self._monotonic = monotonic
        #: Every key that has ever resolved successfully in this process -
        #: the fallback of last resort when the endpoint is unreachable.
        self._known_keys: dict[str, PyJWK] = {}
        self._last_refresh_attempt: float | None = None

    def signing_key_for(self, token: str) -> PyJWK:
        # `get_unverified_header` reads the header without checking the
        # signature - it is used here ONLY to select which key to fetch.
        # The key fetched is what proves the token; no claim is trusted
        # from this call.
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise SigningKeyUnavailableError("token header carries no kid")

        if kid not in self._known_keys:
            now = self._monotonic()
            if (
                self._last_refresh_attempt is not None
                and (now - self._last_refresh_attempt) < self._refresh_cooldown_seconds
            ):
                raise SigningKeyUnavailableError(
                    f"kid {kid!r} is unknown and a JWKS refresh was attempted "
                    f"within the last {self._refresh_cooldown_seconds}s"
                )
            self._last_refresh_attempt = now

        try:
            key = self._client.get_signing_key(kid)
        except PyJWKClientError as exc:
            if kid in self._known_keys:
                return self._known_keys[kid]
            raise SigningKeyUnavailableError(
                f"no signing key available for kid {kid!r}: {exc}"
            ) from exc

        self._known_keys[kid] = key
        return key
