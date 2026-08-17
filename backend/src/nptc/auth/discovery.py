"""OIDC discovery, resolving the realm's JWKS endpoint (issue #43, NFR-07).

A discovery document is fetched before anything about a token is
verified, so if it were allowed to name its own trust anchors the whole
verification chain would be unanchored. Three refusals below exist for
exactly that reason, each with its own test in
``backend/tests/test_auth_discovery.py``.

``NPTC_JWKS_URL`` (``AuthSettings.jwks_url``) skips this module entirely -
air-gapped deployments, and it keeps the offline test suite down to one
local HTTP endpoint per test rather than two.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from nptc.auth.errors import SigningKeyUnavailableError, TokenIssuerError

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def resolve_jwks_url(issuer: str, *, client: httpx.Client) -> str:
    """Fetches ``{issuer}/.well-known/openid-configuration`` and returns
    its ``jwks_uri``, refusing a document that fails any of:

    - ``issuer`` is plain ``http`` and its host is not ``localhost``/
      ``127.0.0.1``/``::1`` (NFR-21) - checked *before* the request is
      made, so a refused configuration is never actually contacted over
      cleartext. Keycloak's dev stack is http-on-localhost, so that one
      case is allowed and documented, not a loophole.
    - the document's own ``issuer`` does not match ``issuer`` exactly.
    - ``jwks_uri``'s scheme/host/port differ from ``issuer``'s - stops a
      tampered or misconfigured document redirecting key retrieval to
      another host.

    A transport failure, a non-2xx response, a non-JSON body, or a
    document missing ``jwks_uri`` all raise ``SigningKeyUnavailableError``
    rather than propagating a raw ``httpx``/``ValueError`` - the whole
    point of ``nptc.auth.errors`` is that a caller (a future FastAPI
    dependency) can catch one exception family and map it to a 401.
    """
    issuer_parts = urlsplit(issuer)
    if issuer_parts.scheme == "http" and issuer_parts.hostname not in _LOCAL_HOSTS:
        raise TokenIssuerError(f"issuer {issuer!r} uses plain http on a non-local host (NFR-21)")

    try:
        response = client.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        document: Any = response.json()
    except httpx.HTTPError as exc:
        raise SigningKeyUnavailableError(
            f"failed to fetch discovery document for issuer {issuer!r}: {exc}"
        ) from exc
    except ValueError as exc:
        raise SigningKeyUnavailableError(
            f"discovery document for issuer {issuer!r} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise SigningKeyUnavailableError(
            f"discovery document for issuer {issuer!r} is not a JSON object"
        )

    document_issuer = document.get("issuer")
    if document_issuer != issuer:
        raise TokenIssuerError(
            f"discovery document issuer {document_issuer!r} does not match "
            f"configured issuer {issuer!r}"
        )

    try:
        jwks_uri = document["jwks_uri"]
    except KeyError as exc:
        raise SigningKeyUnavailableError(
            f"discovery document for issuer {issuer!r} has no jwks_uri"
        ) from exc

    jwks_parts = urlsplit(jwks_uri)
    if (
        jwks_parts.scheme != issuer_parts.scheme
        or jwks_parts.hostname != issuer_parts.hostname
        or jwks_parts.port != issuer_parts.port
    ):
        raise TokenIssuerError(f"jwks_uri {jwks_uri!r} is not same-origin as issuer {issuer!r}")

    return str(jwks_uri)
