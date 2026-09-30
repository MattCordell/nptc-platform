"""Signing-key retrieval for NFR-07 token verification.

A thin wrapper over ``jwt.PyJWKClient`` adding the two behaviours it does not
itself guarantee (ADR-0016):

- **A fallback cache that survives a *transport* outage, but not revocation and
  not forever.** ``PyJWKClient`` re-fetches once its cache lifespan elapses and
  *raises* on failure even when it still holds usable keys. ``SigningKeys``
  keeps every key it has resolved, with the time it was *last confirmed
  present* in a fetch, and falls back to that entry only on
  ``PyJWKClientConnectionError``. A *successful* fetch that no longer lists a
  ``kid`` raises ``PyJWKClientError``/``PyJWKSetError`` and is never papered
  over: a fallback there would keep accepting tokens signed by a key the IdP
  retired. An entry is honoured for ``max_fallback_age_seconds`` only. That
  limit derives from ``cache_seconds`` (default ``10x``) and has no ``NPTC_*``
  variable, because it is a rare-outage safety margin, not routine tuning.
- **A refresh cooldown, covering both an unrecognised ``kid`` and a known
  one.** ``PyJWKClient`` re-fetches the whole JWKS for every ``kid`` it does
  not recognise, so sprayed random ``kid`` values would make the backend
  hammer Keycloak. An unknown ``kid`` seen within ``refresh_cooldown_seconds``
  of the last refresh attempt is refused without any HTTP request. The same
  cooldown covers a *known* ``kid`` once a live fetch has failed, so an IdP
  outage does not add up to ``timeout_seconds`` of latency to every request.

Never a bypass: there is no path from "no key could be obtained" to "accept the
token anyway" anywhere in this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import (
    DecodeError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKSetError,
)

from nptc.auth.errors import SigningKeyUnavailableError, TokenInvalidError


class SigningKeys:
    def __init__(
        self,
        jwks_url: str,
        *,
        cache_seconds: float = 300.0,
        refresh_cooldown_seconds: float = 30.0,
        timeout_seconds: float = 10.0,
        max_fallback_age_seconds: float | None = None,
        client: PyJWKClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # PyJWT 2.14 added its own forced-refresh cooldown (default 30s on
        # real time). It is off on the client built here so this class's
        # cooldown, on the injectable clock, decides when a refresh runs;
        # otherwise a rotated-in key stays unknown until the longer of the
        # two windows ends. An injected `client` keeps whatever it was given.
        self._client = client or PyJWKClient(
            jwks_url, lifespan=cache_seconds, timeout=timeout_seconds, cooldown_duration=0
        )
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        #: A fallback entry older than this counts as unavailable.
        self._max_fallback_age_seconds = (
            max_fallback_age_seconds if max_fallback_age_seconds is not None else cache_seconds * 10
        )
        self._monotonic = monotonic
        #: Every key that has resolved in this process, with the time it was
        #: *last confirmed present* in a fetch (reset on every
        #: re-confirmation). The fallback when the endpoint is unreachable.
        self._known_keys: dict[str, tuple[PyJWK, float]] = {}
        #: Last unknown-kid refresh attempt, whatever its outcome.
        self._last_refresh_attempt: float | None = None
        #: Last live fetch that failed to reach the endpoint
        #: (`PyJWKClientConnectionError`). Separate from
        #: `_last_refresh_attempt`, so a known kid skips its retry only when
        #: the endpoint is known to be down, not because some other attempt
        #: happened recently.
        self._last_failed_fetch: float | None = None

    def signing_key_for(self, token: str) -> PyJWK:
        # `get_unverified_header` reads the header without checking the
        # signature. It is used ONLY to select which key to fetch: the key
        # proves the token, and no claim is trusted from this call.
        try:
            header = jwt.get_unverified_header(token)
        except DecodeError as exc:
            raise TokenInvalidError(f"malformed token: {exc}") from exc
        kid = header.get("kid")
        if not kid:
            raise SigningKeyUnavailableError("token header carries no kid")

        now = self._monotonic()
        if kid not in self._known_keys:
            if (
                self._last_refresh_attempt is not None
                and (now - self._last_refresh_attempt) < self._refresh_cooldown_seconds
            ):
                raise SigningKeyUnavailableError(
                    f"kid {kid!r} is unknown and a JWKS refresh was attempted "
                    f"within the last {self._refresh_cooldown_seconds}s"
                )
            self._last_refresh_attempt = now
        elif (
            self._last_failed_fetch is not None
            and (now - self._last_failed_fetch) < self._refresh_cooldown_seconds
        ):
            return self._fallback_key(
                kid, f"JWKS endpoint failed within the last {self._refresh_cooldown_seconds}s"
            )

        try:
            key = self._client.get_signing_key(kid)
        except PyJWKClientConnectionError as exc:
            self._last_failed_fetch = self._monotonic()
            return self._fallback_key(kid, str(exc))
        except (PyJWKClientError, PyJWKSetError) as exc:
            # The fetch succeeded but this kid is not in the published set
            # (revoked or rotated away, or an empty set, which PyJWT raises as
            # PyJWKSetError rather than PyJWKClientError). Never fall back
            # here: that would keep accepting a key the IdP retired.
            raise SigningKeyUnavailableError(
                f"no signing key published for kid {kid!r}: {exc}"
            ) from exc

        self._known_keys[kid] = (key, self._monotonic())
        return key

    def _fallback_key(self, kid: str, reason: str) -> PyJWK:
        cached = self._known_keys.get(kid)
        if cached is None:
            raise SigningKeyUnavailableError(
                f"JWKS endpoint unreachable and no cached key for kid {kid!r}: {reason}"
            )

        key, last_confirmed = cached
        age = self._monotonic() - last_confirmed
        if age > self._max_fallback_age_seconds:
            raise SigningKeyUnavailableError(
                f"JWKS endpoint unreachable and cached key for kid {kid!r} was last "
                f"confirmed {age:.0f}s ago, past the {self._max_fallback_age_seconds:.0f}s "
                f"fallback limit: {reason}"
            )
        return key
