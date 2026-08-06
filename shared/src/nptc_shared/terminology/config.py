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

#: Codes per ``ValueSet/$expand`` in the FR-52 bulk pass. FR-52 says "start
#: around 200 to 500 codes and tune"; 300 is the midpoint, and ADR-0005
#: records both the reasoning and the procedure for tuning it against a
#: specific server.
DEFAULT_CHUNK_SIZE = 300

#: Concurrent requests in the FR-52 targeted second pass. FR-52 says "default
#: conservatively" - four is conservative for a shared public terminology
#: server, and ADR-0005 records why it is not higher.
DEFAULT_MAX_CONCURRENCY = 4

_BASE_URL_VAR = "NPTC_TX_BASE_URL"
_TOKEN_VAR = "NPTC_TX_TOKEN"
_TIMEOUT_VAR = "NPTC_TX_TIMEOUT_SECONDS"
_MAX_RETRIES_VAR = "NPTC_TX_MAX_RETRIES"
_CHUNK_SIZE_VAR = "NPTC_TX_CHUNK_SIZE"
_MAX_CONCURRENCY_VAR = "NPTC_TX_MAX_CONCURRENCY"


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
    chunk_size: int = DEFAULT_CHUNK_SIZE
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    def __post_init__(self) -> None:
        # httpx joins base_url's raw_path with the request path; a base URL
        # of ".../fhir" without a trailing slash silently drops the "/fhir"
        # segment for some join shapes. Normalising here means every caller
        # gets the correct join regardless of how the value arrived.
        if not self.base_url.endswith("/"):
            object.__setattr__(self, "base_url", f"{self.base_url}/")
        # Rejected here rather than at the sweep's call site: a chunk size of
        # zero makes no progress at all, and a negative one silently inverts
        # a slice into an empty chunk - either way the sweep would report a
        # clean catalogue it never actually checked, which is the exact FR-54
        # hazard (an outage that reads as a clean result).
        if self.chunk_size < 1:
            raise TerminologyConfigError(f"chunk_size must be at least 1, got {self.chunk_size}")
        if self.max_concurrency < 1:
            raise TerminologyConfigError(
                f"max_concurrency must be at least 1, got {self.max_concurrency}"
            )

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

        chunk_size_raw = source.get(_CHUNK_SIZE_VAR)
        if chunk_size_raw:
            kwargs["chunk_size"] = _parse_int(_CHUNK_SIZE_VAR, chunk_size_raw)

        max_concurrency_raw = source.get(_MAX_CONCURRENCY_VAR)
        if max_concurrency_raw:
            kwargs["max_concurrency"] = _parse_int(_MAX_CONCURRENCY_VAR, max_concurrency_raw)

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
