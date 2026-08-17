"""Signing-key retrieval for NFR-07 token verification (issue #43).

A thin wrapper over ``jwt.PyJWKClient`` adding the two behaviours it does
not itself guarantee:

- **A fallback cache that survives a *transport* outage, but not
  revocation and not forever.** ``PyJWKClient``'s own cache (``lifespan``
  seconds) only ever holds the *most recent* fetch. Once that lifespan
  elapses, ``PyJWKClient`` re-fetches on the next lookup and *raises* on
  failure even if it still, logically, has usable key material from the
  previous fetch. This module's own ``dict[str, tuple[PyJWK, float]]`` is
  what stops an *expired* cache plus a down IdP from rejecting a token
  whose key this process has already seen and validated before -
  pinning ``pyjwt[crypto]>=2.13`` (see backend/pyproject.toml) is the
  companion fix stopping a failed fetch from *wiping* PyJWKClient's own
  cache outright (GHSA-fhv5-28vv-h8m8).

  This fallback is deliberately narrow in two ways. First, it only
  triggers on ``PyJWKClientConnectionError`` (the endpoint could not be
  reached at all) - a *successful* fetch that simply no longer lists a
  given ``kid`` (the operator revoked or rotated it away, or the whole
  set is empty) raises ``PyJWKClientError``/``PyJWKSetError`` too, but
  that is a case this module must **not** paper over: falling back there
  would keep accepting tokens signed by a key the IdP has explicitly
  retired, for the rest of the process's lifetime. Second, a fallback
  entry is only honoured for ``max_fallback_age_seconds`` after it was
  *last confirmed present* in a fetch (not merely first seen - a key
  seen again in every fetch keeps its age reset, so a key still being
  served right up until an outage starts gets the full fallback window,
  not whatever was left over from when it first appeared) - "survive a
  blip" must not silently become "survive forever" once an outage
  outlasts a rotation window. Not independently configurable via an
  ``NPTC_*`` environment variable, unlike the other JWKS knobs
  (``AuthSettings``/``configuration.md``): it derives from
  ``cache_seconds`` (default ``10x``) rather than adding a fifth knob for
  what is a rare-outage safety margin, not routine deployment tuning.
- **A refresh cooldown, covering both an unrecognised ``kid`` and a
  known one.** ``PyJWKClient`` re-fetches the whole JWKS on every ``kid``
  it doesn't recognise, so an unauthenticated attacker spraying random
  ``kid`` values makes the backend hammer Keycloak; an unknown ``kid``
  seen within ``refresh_cooldown_seconds`` of the last refresh attempt is
  refused without any HTTP request at all, and genuine key rotation
  still lands within one cooldown window. The same cooldown also covers
  a *known* ``kid`` once a live fetch has failed: without this, once
  ``PyJWKClient``'s own cache lifespan expires, **every** verification
  during an outage would re-attempt the fetch and block for up to
  ``timeout_seconds`` before falling back - turning an IdP outage into a
  request-latency outage even though the key is already cached. Safe
  even if the endpoint has since recovered: a successful fetch refreshes
  ``PyJWKClient``'s own cache for ``cache_seconds``, so skipping a retry
  during the cooldown only delays noticing a real recovery by at most
  one cooldown window.

Never a bypass: there is no path from "no key could be obtained" to
"accept the token anyway" anywhere in this module.
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
        self._client = client or PyJWKClient(
            jwks_url, lifespan=cache_seconds, timeout=timeout_seconds
        )
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        #: Not indefinite: a transport-outage fallback entry older than
        #: this is treated as unavailable rather than trusted forever.
        self._max_fallback_age_seconds = (
            max_fallback_age_seconds if max_fallback_age_seconds is not None else cache_seconds * 10
        )
        self._monotonic = monotonic
        #: Every key that has ever resolved successfully in this process,
        #: with the time it was *last confirmed present* in a fetch (reset
        #: on every re-confirmation, not just the first) - the fallback of
        #: last resort when the endpoint is unreachable, aged out after
        #: `_max_fallback_age_seconds`.
        self._known_keys: dict[str, tuple[PyJWK, float]] = {}
        #: Sprayed-kid cooldown: last time an unknown-kid refresh was
        #: attempted, regardless of outcome.
        self._last_refresh_attempt: float | None = None
        #: Outage cooldown: last time a live fetch actually failed to
        #: reach the endpoint (`PyJWKClientConnectionError`) - separate
        #: from `_last_refresh_attempt` because a known kid should only
        #: skip its own retry when the endpoint is known to be down, not
        #: merely because some other, successful attempt happened
        #: recently.
        self._last_failed_fetch: float | None = None

    def signing_key_for(self, token: str) -> PyJWK:
        # `get_unverified_header` reads the header without checking the
        # signature - it is used here ONLY to select which key to fetch.
        # The key fetched is what proves the token; no claim is trusted
        # from this call.
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
            # The fetch succeeded but this kid is genuinely not in the
            # published set - e.g. the operator revoked or rotated it
            # away, or nothing at all is published (PyJWKSetError, a
            # separate exception family from PyJWKClientError for an
            # empty key set). Never fall back here: that would keep
            # accepting tokens signed by a key the IdP has explicitly
            # retired.
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
