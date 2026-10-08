"""Per-IP rate limiting of anonymous API requests: NFR-24's first layer, FR-22's refusal.

**A middleware, not a dependency.** It runs before routing, so a request it refuses opens no
database session and verifies no token, and it covers the routes FastAPI serves itself
(`/docs`, `/openapi.json`). It sits inside `CORSMiddleware`, so the 429 carries the CORS headers
a browser needs to read it.

**Two budgets per address, with the same limit and window.** A request with no `Authorization`
header (the test `dependencies.current_principal` uses to return `ANONYMOUS`) spends the
anonymous budget. A request that carries one spends nothing unless the route answers 401: a
rejected token costs the server a database session, so it is charged to a second, separate
budget, and an address that has used it up is refused whatever it sends next. The limiter
verifies no token. A valid credential is never counted, and anonymous traffic never refuses a
signed-in user. The per-user layer (NFR-24) is a separate control.

**The budget.** A fixed window per address. The window opens at the first counted request, and
`Retry-After` is the time left in it, so a caller who waits that long is served. Counters live in
this process: a second worker or replica keeps its own, so the budget a caller sees is the limit
times the number of processes. `docs/architecture/public-api.md` records this.
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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nptc.api.client_ip import IPAddress, IPNetwork, bucket_key, resolve_client_address
from nptc.api.prefix import API_PREFIX

RATE_LIMITED_DETAIL: Final = (
    "Too many requests from this address. Wait for the time in the Retry-After header, then "
    "try again. To fetch the whole catalogue, use the bulk release artefacts instead."
)

#: Where a caller whose address is not an IP lands. Sharing one budget is the safe answer: a
#: bypass would let any caller on such a deployment opt out of the limit.
_UNKNOWN_ADDRESS_KEY: Final = "unknown"

#: Prefixes the key of the budget for credentials a route rejected, kept apart from the
#: anonymous budget so a signed-in user is never refused because of anonymous traffic.
_REJECTED_KEY_PREFIX: Final = "rejected:"

#: No liveness endpoint exists, so the compose healthcheck probes this path from loopback. A
#: refused probe would mark a healthy container unhealthy.
HEALTH_PROBE_PATH: Final = f"{API_PREFIX}/openapi.json"


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

        key = _UNKNOWN_ADDRESS_KEY if address is None else bucket_key(address)
        retry_after: int | None = None
        if _header(headers, b"authorization") is not None:
            rejected_key = _REJECTED_KEY_PREFIX + key
            retry_after = self._time_left(rejected_key)
            send = self._charging_rejections(send, rejected_key)
        elif not _is_health_probe(address, scope):
            retry_after = self._charge(key)

        if retry_after is not None:
            response = JSONResponse(
                {"detail": RATE_LIMITED_DETAIL, "bulk_artefacts": self._bulk_artefacts_url},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _charging_rejections(self, send: Send, key: str) -> Send:
        """Counts a 401 against `key` as it leaves, so the budget is spent only by credentials
        the route actually rejected, with no token verified here."""

        async def charging(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401:
                self._count(key, self._monotonic())
            await send(message)

        return charging

    def _charge(self, key: str) -> int | None:
        """Counts one request. `None` when it is within budget, otherwise the seconds to wait."""
        retry_after = self._time_left(key)
        if retry_after is None:
            self._count(key, self._monotonic())
        return retry_after

    def _time_left(self, key: str) -> int | None:
        """`None` while `key` has budget, otherwise the whole seconds until its window closes:
        rounded up so waiting that long always works, and at least 1 so a client never
        retries immediately."""
        now = self._monotonic()
        self._sweep(now)
        window = self._windows.get(key)
        if window is None or now >= window.opened_at + self._window_seconds:
            return None
        if window.count < self._limit:
            return None
        return max(1, math.ceil(window.opened_at + self._window_seconds - now))

    def _count(self, key: str, now: float) -> None:
        window = self._windows.get(key)
        if window is None or now >= window.opened_at + self._window_seconds:
            self._windows[key] = _Window(opened_at=now, count=1)
        else:
            window.count += 1

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


def _is_health_probe(address: IPAddress | None, scope: Scope) -> bool:
    return address is not None and address.is_loopback and scope["path"] == HEALTH_PROBE_PATH


def _header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str | None:
    """Every line of the header, joined in order as RFC 9110 defines them to mean: a proxy
    may append its own `X-Forwarded-For` line rather than extend the caller's."""
    values = [value.decode("latin-1") for key, value in headers if key == name]
    return ", ".join(values) if values else None


_HTTP_METHODS: Final = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_REFUSAL_REF: Final = {"$ref": "#/components/schemas/RateLimitedResponse"}
_REFUSAL_DESCRIPTION: Final = (
    "An address exceeded its request budget (FR-22): an anonymous caller its anonymous budget, "
    "or a caller whose credentials the API kept rejecting its budget for rejected credentials. "
    "Wait for the number of seconds in `Retry-After`, then try again. A valid credential is "
    "never counted. The body's `bulk_artefacts` names where to fetch the whole catalogue "
    "instead."
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
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation.setdefault("responses", {}).setdefault(
                "429",
                {
                    "description": _REFUSAL_DESCRIPTION,
                    "headers": {"Retry-After": _RETRY_AFTER_HEADER},
                    "content": {"application/json": {"schema": _REFUSAL_REF}},
                },
            )
