"""Per-IP rate limiting of anonymous API requests: NFR-24's first layer, FR-22's refusal.

**A middleware, not a dependency.** It runs before routing, so a request it refuses opens no
database session and verifies no token, and it covers the routes FastAPI serves itself
(`/docs`, `/openapi.json`). It sits inside `CORSMiddleware`, so the 429 carries the CORS headers
a browser needs to read it.

**Who counts.** A request with no `Authorization` header. The same test
`dependencies.current_principal` uses to return `ANONYMOUS`. A request that carries any
`Authorization` header is never counted, even when its token is later refused: the refusal costs
the caller a signature check, and verifying tokens here would charge every refused request that
cost twice. The per-user layer (NFR-24) is a separate control.

**The budget.** A fixed window per address. The window opens at the first counted request, and
`Retry-After` is the time left in it, so a caller who waits that long is served. Counters live in
this process: a second worker or replica keeps its own, so the budget a caller sees is the limit
times the number of processes. `docs/architecture/public-api.md` records this.

**Loopback is never limited.** The compose healthcheck calls the API from 127.0.0.1, and a
limited healthcheck would restart a healthy container.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from nptc.api.client_ip import IPNetwork, bucket_key, resolve_client_address

RATE_LIMITED_DETAIL: Final = (
    "Too many requests from this address. Wait for the time in the Retry-After header, then "
    "try again. To fetch the whole catalogue, use the bulk release artefacts instead."
)

#: Where a caller whose address is not an IP lands. Sharing one budget is the safe answer: a
#: bypass would let any caller on such a deployment opt out of the limit.
_UNKNOWN_ADDRESS_KEY: Final = "unknown"


class RateLimitedResponse(BaseModel):
    detail: str = Field(description="One sentence saying what to do next.")
    bulk_artefacts: str = Field(
        description="Where to fetch the bulk release artefacts instead of paging the API."
    )


@dataclass
class _Window:
    opened_at: float
    count: int


class AnonymousRateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        window_seconds: int,
        bulk_artefacts_url: str,
        trusted_proxies: Sequence[IPNetwork],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._limit = limit
        self._window_seconds = window_seconds
        self._bulk_artefacts_url = bulk_artefacts_url
        self._trusted_proxies = tuple(trusted_proxies)
        self._monotonic = monotonic
        self._windows: dict[str, _Window] = {}
        self._next_sweep = monotonic() + window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope["headers"]
        client = scope.get("client")
        address = resolve_client_address(
            client[0] if client else None,
            _header(headers, b"x-forwarded-for"),
            self._trusted_proxies,
        )
        # `dependencies._client_ip` reads this, so the audit log records the same address.
        scope.setdefault("state", {})["client_address"] = None if address is None else str(address)

        anonymous = _header(headers, b"authorization") is None
        if anonymous and (address is None or not address.is_loopback):
            retry_after = self._charge(
                _UNKNOWN_ADDRESS_KEY if address is None else bucket_key(address)
            )
            if retry_after is not None:
                response = JSONResponse(
                    {"detail": RATE_LIMITED_DETAIL, "bulk_artefacts": self._bulk_artefacts_url},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    def _charge(self, key: str) -> int | None:
        """Counts one request. `None` when it is within budget, otherwise the whole seconds
        until the window closes: rounded up so waiting that long always works, and at least 1
        so a client never retries immediately."""
        now = self._monotonic()
        self._sweep(now)
        window = self._windows.get(key)
        if window is None or now >= window.opened_at + self._window_seconds:
            self._windows[key] = _Window(opened_at=now, count=1)
            return None
        if window.count < self._limit:
            window.count += 1
            return None
        return max(1, math.ceil(window.opened_at + self._window_seconds - now))

    def _sweep(self, now: float) -> None:
        """Drops closed windows once per window length, so memory follows the callers seen in
        the last window rather than every caller since start-up."""
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._window_seconds
        closed = [
            key
            for key, window in self._windows.items()
            if now >= window.opened_at + self._window_seconds
        ]
        for key in closed:
            del self._windows[key]


def _header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return None


_REFUSAL_REF: Final = {"$ref": "#/components/schemas/RateLimitedResponse"}
_REFUSAL_DESCRIPTION: Final = (
    "An anonymous caller exceeded the per-address request budget (FR-22). Wait for the number "
    "of seconds in `Retry-After`, then try again. Requests that carry an `Authorization` header "
    "are never refused for this reason. The body's `bulk_artefacts` names where to fetch the "
    "whole catalogue instead."
)
_RETRY_AFTER_HEADER: Final = {
    "description": "Whole seconds until the caller's request budget is available again.",
    "schema": {"type": "integer", "minimum": 1},
}


def declare_rate_limit_refusal(app: FastAPI) -> None:
    """Adds the 429 to every operation in the OpenAPI document.

    A middleware cannot declare a response, and every route is reachable without a credential,
    so a generated client has to type the refusal on all of them.
    """
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original()
        _add_rate_limit_refusal(schema)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _add_rate_limit_refusal(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["RateLimitedResponse"] = RateLimitedResponse.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            operation.setdefault("responses", {}).setdefault(
                "429",
                {
                    "description": _REFUSAL_DESCRIPTION,
                    "headers": {"Retry-After": _RETRY_AFTER_HEADER},
                    "content": {"application/json": {"schema": _REFUSAL_REF}},
                },
            )
