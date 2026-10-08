"""Per-IP rate limiting of anonymous requests (FR-22, NFR-24 layer 1).

The limiter is a middleware, so almost every case runs against `create_app` with no
database: `/api/v1/openapi.json` is served by FastAPI and touches none. One case goes
through the full auth chain to show a signed-in caller is not counted.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nptc.api.app import create_app
from nptc.api.dependencies import request_audit_context
from nptc.api.prefix import API_PREFIX
from nptc.api.rate_limit import RATE_LIMITED_DETAIL, AnonymousRateLimitMiddleware
from nptc.auth.permissions import Role


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")

build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp
hermetic_api_settings = _api_support.hermetic_api_settings

_COUNTED = f"{API_PREFIX}/openapi.json"
_BULK_URL = "https://releases.example.org/nptc"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _app_client(
    clock: _Clock,
    *,
    limit: int = 2,
    window: int = 60,
    trusted_proxies: str = "",
    peer: tuple[str, int] = ("203.0.113.7", 50000),
) -> TestClient:
    app = create_app(
        settings=hermetic_api_settings(
            anon_rate_limit_requests=limit,
            anon_rate_limit_window_seconds=window,
            bulk_artefacts_url=_BULK_URL,
            trusted_proxies=trusted_proxies,
        ),
        rate_limit_clock=clock,
    )
    return TestClient(app, client=peer, raise_server_exceptions=False)


def _client_for(app_client: TestClient, peer: tuple[str, int]) -> TestClient:
    return TestClient(app_client.app, client=peer, raise_server_exceptions=False)


@pytest.mark.req("FR-22")
@pytest.mark.req("NFR-24")
def test_an_anonymous_caller_over_the_limit_gets_a_429_naming_the_bulk_artefacts() -> None:
    client = _app_client(_Clock(), limit=2)

    assert client.get(_COUNTED).status_code == 200
    assert client.get(_COUNTED).status_code == 200
    refused = client.get(_COUNTED)

    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.json() == {"detail": RATE_LIMITED_DETAIL, "bulk_artefacts": _BULK_URL}


@pytest.mark.req("FR-22")
def test_the_429_body_does_not_echo_the_request() -> None:
    client = _app_client(_Clock(), limit=1)
    client.get(_COUNTED)

    refused = client.get(f"{_COUNTED}?q=secret-search-term", headers={"User-Agent": "probe-agent"})

    assert "secret-search-term" not in refused.text
    assert "probe-agent" not in refused.text


@pytest.mark.req("FR-22")
def test_a_caller_who_waits_the_advertised_retry_after_is_served() -> None:
    """The header is a promise: waiting exactly that long works, one second less does not."""
    clock = _Clock()
    client = _app_client(clock, limit=2, window=60)
    client.get(_COUNTED)
    client.get(_COUNTED)
    clock.now += 10
    refused = client.get(_COUNTED)
    retry_after = int(refused.headers["Retry-After"])
    assert retry_after == 50

    clock.now += retry_after - 1
    assert client.get(_COUNTED).status_code == 429

    clock.now += 1
    assert client.get(_COUNTED).status_code == 200


@pytest.mark.req("FR-22")
def test_retry_after_rounds_up_so_waiting_it_is_always_enough() -> None:
    clock = _Clock()
    client = _app_client(clock, limit=1, window=60)
    client.get(_COUNTED)
    clock.now += 10.5

    refused = client.get(_COUNTED)

    assert refused.headers["Retry-After"] == "50"
    clock.now += 49.5
    assert client.get(_COUNTED).status_code == 200


@pytest.mark.req("FR-22")
def test_retry_after_is_never_below_one_second() -> None:
    clock = _Clock()
    client = _app_client(clock, limit=1, window=60)
    client.get(_COUNTED)
    clock.now += 59.9

    assert client.get(_COUNTED).headers["Retry-After"] == "1"


@pytest.mark.req("FR-22")
def test_a_refused_request_does_not_extend_the_window() -> None:
    clock = _Clock()
    client = _app_client(clock, limit=1, window=60)
    client.get(_COUNTED)
    for _ in range(5):
        clock.now += 10
        assert client.get(_COUNTED).status_code == 429

    clock.now += 10

    assert client.get(_COUNTED).status_code == 200


@pytest.mark.req("NFR-24")
def test_requests_carrying_an_authorization_header_are_never_counted() -> None:
    """Including one whose token is junk: the 401 it earns downstream already cost the
    caller a signature check, and the limiter does not verify tokens."""
    client = _app_client(_Clock(), limit=1)

    for _ in range(5):
        assert (
            client.get(_COUNTED, headers={"Authorization": "Bearer not-a-token"}).status_code == 200
        )

    assert client.get(_COUNTED).status_code == 200
    assert client.get(_COUNTED).status_code == 429


@pytest.mark.req("NFR-24")
def test_each_address_has_its_own_budget() -> None:
    clock = _Clock()
    first = _app_client(clock, limit=1, peer=("203.0.113.7", 1))
    second = _client_for(first, ("198.51.100.9", 1))

    assert first.get(_COUNTED).status_code == 200
    assert first.get(_COUNTED).status_code == 429
    assert second.get(_COUNTED).status_code == 200


@pytest.mark.req("NFR-24")
def test_loopback_is_never_limited() -> None:
    """The compose healthcheck calls from 127.0.0.1 every ten seconds."""
    client = _app_client(_Clock(), limit=1, peer=("127.0.0.1", 1))

    assert all(client.get(_COUNTED).status_code == 200 for _ in range(5))


@pytest.mark.req("NFR-24")
def test_callers_with_no_usable_address_share_one_budget_rather_than_none() -> None:
    clock = _Clock()
    client = _app_client(clock, limit=1, peer=("testclient", 50000))

    assert client.get(_COUNTED).status_code == 200
    assert client.get(_COUNTED).status_code == 429


@pytest.mark.req("NFR-24")
def test_a_trusted_proxy_gives_each_caller_behind_it_a_budget() -> None:
    clock = _Clock()
    caddy = ("10.0.0.5", 4000)
    client = _app_client(clock, limit=1, trusted_proxies="10.0.0.0/8", peer=caddy)

    assert client.get(_COUNTED, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 200
    assert client.get(_COUNTED, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429
    assert client.get(_COUNTED, headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 200


@pytest.mark.req("NFR-24")
def test_an_untrusted_peer_cannot_pick_its_own_budget_with_a_forged_header() -> None:
    """The negative case: with the header believed, a caller would rotate through
    addresses and never meet the limit."""
    client = _app_client(
        _Clock(), limit=1, trusted_proxies="10.0.0.0/8", peer=("198.51.100.9", 4000)
    )

    assert client.get(_COUNTED, headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 200
    assert client.get(_COUNTED, headers={"X-Forwarded-For": "192.0.2.2"}).status_code == 429


@pytest.mark.req("NFR-24")
def test_a_proxy_that_forwards_loopback_does_not_earn_an_exemption_for_a_forger() -> None:
    """Only the address the trusted proxy appended is believed, so `127.0.0.1` written by the
    caller at the left of the header is not an exemption."""
    client = _app_client(_Clock(), limit=1, trusted_proxies="10.0.0.0/8", peer=("10.0.0.5", 4000))
    headers = {"X-Forwarded-For": "127.0.0.1, 203.0.113.7"}

    assert client.get(_COUNTED, headers=headers).status_code == 200
    assert client.get(_COUNTED, headers=headers).status_code == 429


@pytest.mark.req("FR-22")
def test_the_429_is_readable_by_the_browser_that_called_cross_origin() -> None:
    client = _app_client(_Clock(), limit=1)
    origin = {"Origin": "http://localhost:5173"}
    client.get(_COUNTED, headers=origin)

    refused = client.get(_COUNTED, headers=origin)

    assert refused.status_code == 429
    assert refused.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "Retry-After" in refused.headers["access-control-expose-headers"]


@pytest.mark.req("NFR-24")
def test_a_preflight_is_answered_by_cors_and_is_not_counted() -> None:
    client = _app_client(_Clock(), limit=1)
    preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}

    for _ in range(3):
        assert client.options(_COUNTED, headers=preflight).status_code == 200

    assert client.get(_COUNTED).status_code == 200


@pytest.mark.req("NFR-24")
def test_closed_windows_are_swept_so_memory_follows_recent_callers() -> None:
    clock = _Clock()

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    middleware = AnonymousRateLimitMiddleware(
        Starlette(routes=[Route("/", ok)]),
        limit=5,
        window_seconds=60,
        bulk_artefacts_url=_BULK_URL,
        trusted_proxies=(),
        monotonic=clock,
    )
    for last_octet in range(1, 4):
        TestClient(middleware, client=(f"203.0.113.{last_octet}", 1)).get("/")
    assert len(middleware._windows) == 3

    clock.now += 61
    TestClient(middleware, client=("198.51.100.9", 1)).get("/")

    assert list(middleware._windows) == ["198.51.100.9"]


@pytest.mark.req("NFR-08")
def test_the_audit_context_records_the_address_the_limiter_decided() -> None:
    """Behind Caddy the connecting address is the proxy's; the audit log should name the caller."""
    request = Request(
        {
            "type": "http",
            "headers": [],
            "client": ("10.0.0.5", 4000),
            "state": {"client_address": "203.0.113.7"},
        }
    )

    assert request_audit_context(request).actor_ip == "203.0.113.7"


@pytest.mark.req("FR-22")
def test_every_operation_declares_the_429_with_its_retry_after_header() -> None:
    schema = _app_client(_Clock()).get(_COUNTED).json()

    operations = [operation for path in schema["paths"].values() for operation in path.values()]
    assert operations
    for operation in operations:
        refusal = operation["responses"]["429"]
        assert refusal["headers"]["Retry-After"]["schema"]["type"] == "integer"
        assert refusal["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/RateLimitedResponse"
        }
    body = schema["components"]["schemas"]["RateLimitedResponse"]
    assert set(body["required"]) == {"detail", "bulk_artefacts"}


@pytest.fixture
def limited_api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(
        app_db,
        api_settings=hermetic_api_settings(
            anon_rate_limit_requests=2, anon_rate_limit_window_seconds=60
        ),
        rate_limit_clock=_Clock(),
    )


@pytest.mark.req("NFR-24")
@pytest.mark.integration
def test_a_signed_in_caller_is_not_charged_to_the_anonymous_budget(
    limited_api: ApiTestApp,
) -> None:
    token = limited_api.token_for_role(
        subject="rate-limit-user", role=Role.ADMINISTRATOR, replace_roles=True
    )

    for _ in range(6):
        assert limited_api.get("/catalogue/entries", token=token).status_code == 200

    assert limited_api.get("/catalogue/entries").status_code == 200
    assert limited_api.get("/catalogue/entries").status_code == 200
    assert limited_api.get("/catalogue/entries").status_code == 429
