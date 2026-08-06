"""Configuration for the FR-53 terminology client.

Explicit ``os.environ`` reads, not a settings framework: ``pydantic-settings``
is a backend-only dependency (ADR-0001), and pulling pydantic into ``shared``
would pull it into the transform, which has no other use for it. ``from_env``
takes the environment as an argument so a test configures it by passing a
dict, never by mutating the process.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from nptc_shared.terminology.errors import TerminologyConfigError

DEFAULT_BASE_URL = "https://tx.ontoserver.csiro.au/fhir/"

_BASE_URL_VAR = "NPTC_TX_BASE_URL"
_TOKEN_VAR = "NPTC_TX_TOKEN"
_TIMEOUT_VAR = "NPTC_TX_TIMEOUT_SECONDS"
_MAX_RETRIES_VAR = "NPTC_TX_MAX_RETRIES"


@dataclass(frozen=True, slots=True)
class TerminologyConfig:
    """Everything an ``OntoserverClient`` needs to reach a terminology server.

    ``bearer_token`` is ``repr=False`` - a frozen dataclass's generated
    ``__repr__`` would otherwise put a secret into any traceback or log line
    (NFR-26, NFR-35).
    """

    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    bearer_token: str | None = field(default=None, repr=False)
    max_retries: int = 3
    backoff_base_seconds: float = 0.5
    max_backoff_seconds: float = 30.0

    def __post_init__(self) -> None:
        # httpx joins base_url's raw_path with the request path; a base URL
        # of ".../fhir" without a trailing slash silently drops the "/fhir"
        # segment for some join shapes. Normalising here means every caller
        # gets the correct join regardless of how the value arrived.
        if not self.base_url.endswith("/"):
            object.__setattr__(self, "base_url", f"{self.base_url}/")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TerminologyConfig:
        """Builds a config from environment variables (default: ``os.environ``).

        An empty ``NPTC_TX_TOKEN`` is treated as unset, so a ``.env`` file can
        carry the key with no value without accidentally sending
        ``Authorization: Bearer``. A malformed numeric value raises
        ``TerminologyConfigError`` naming the variable - it never falls back
        to the default, which would hide a deployment typo indefinitely.
        """
        source = env if env is not None else os.environ

        kwargs: dict[str, object] = {}

        base_url = source.get(_BASE_URL_VAR)
        if base_url:
            kwargs["base_url"] = base_url

        token = source.get(_TOKEN_VAR)
        if token:
            kwargs["bearer_token"] = token

        timeout_raw = source.get(_TIMEOUT_VAR)
        if timeout_raw:
            kwargs["timeout_seconds"] = _parse_float(_TIMEOUT_VAR, timeout_raw)

        max_retries_raw = source.get(_MAX_RETRIES_VAR)
        if max_retries_raw:
            kwargs["max_retries"] = _parse_int(_MAX_RETRIES_VAR, max_retries_raw)

        return cls(**kwargs)  # type: ignore[arg-type]


def _parse_float(name: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise TerminologyConfigError(f"{name}={raw!r} is not a valid number") from exc


def _parse_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise TerminologyConfigError(f"{name}={raw!r} is not a valid integer") from exc
