"""FR-27: the supporting-reference check and its SSRF guards.

No test here opens a socket (NFR-37). A fake resolver supplies addresses and
`httpx.MockTransport` supplies responses. The autouse guard below turns any real
connection into a failure at the call, naming the requirement. A local HTTP server is
no alternative: the guard under test refuses 127.0.0.1 by design.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime

import httpx
import pytest

from nptc.api import dependencies
from nptc.submissions import reference_check
from nptc.submissions.reference_check import (
    MAX_REDIRECTS,
    OVERALL_DEADLINE_SECONDS,
    HttpReferenceChecker,
    ReferenceCheckFailedError,
    ReferenceCheckUnavailableError,
    ReferenceFailure,
    is_public_address,
    system_resolver,
    tcp_probe,
)
from nptc_shared.terminology import TerminologyConfigError

pytestmark = pytest.mark.req("FR-27")

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"
FIXED_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-37. Scoped to this module: the rest of `backend/tests` needs loopback for Postgres."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("test_reference_check must not touch the network (NFR-37)")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)


class FakeResolver:
    """Answers from a table of host to addresses, and records every call."""

    def __init__(self, table: Mapping[str, Sequence[str] | Exception]) -> None:
        self._table = table
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> Sequence[str]:
        self.calls.append((host, port))
        answer = self._table[host]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Probe:
    def __init__(self, reachable: bool) -> None:
        self.reachable = reachable
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.reachable


def make_checker(
    handler: Callable[[httpx.Request], httpx.Response],
    resolver: FakeResolver | None = None,
    *,
    probe: Probe | None = None,
    monotonic: Callable[[], float] | None = None,
) -> HttpReferenceChecker:
    kwargs: dict[str, object] = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return HttpReferenceChecker(
        resolver=resolver or FakeResolver({"example.test": [PUBLIC_V4]}),
        transport=httpx.MockTransport(handler),
        probe=probe or Probe(True),
        clock=lambda: FIXED_NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


def refusal_of(checker: HttpReferenceChecker, url: str) -> ReferenceCheckFailedError:
    with pytest.raises(ReferenceCheckFailedError) as caught:
        checker.check(url)
    return caught.value


# --- the address classes ---------------------------------------------------

REFUSED_ADDRESSES = [
    pytest.param("127.0.0.1", id="ipv4-loopback"),
    pytest.param("127.255.255.254", id="ipv4-loopback-high"),
    pytest.param("10.0.0.1", id="private-10"),
    pytest.param("172.16.0.1", id="private-172-16"),
    pytest.param("172.31.255.254", id="private-172-31"),
    pytest.param("192.168.1.1", id="private-192-168"),
    pytest.param("169.254.169.254", id="link-local-cloud-metadata"),
    pytest.param("169.254.0.1", id="link-local"),
    pytest.param("240.0.0.1", id="reserved"),
    pytest.param("255.255.255.255", id="broadcast"),
    pytest.param("224.0.0.1", id="multicast"),
    pytest.param("239.255.255.250", id="multicast-high"),
    pytest.param("0.0.0.0", id="unspecified"),
    pytest.param("100.64.0.1", id="shared-address-space"),
    pytest.param("::1", id="ipv6-loopback"),
    pytest.param("::", id="ipv6-unspecified"),
    pytest.param("fc00::1", id="ipv6-unique-local"),
    pytest.param("fd12:3456:789a::1", id="ipv6-unique-local-fd"),
    pytest.param("fe80::1", id="ipv6-link-local"),
    pytest.param("ff02::1", id="ipv6-multicast"),
    pytest.param("::ffff:127.0.0.1", id="ipv4-mapped-loopback"),
    pytest.param("::ffff:10.0.0.1", id="ipv4-mapped-private"),
    pytest.param("::ffff:169.254.169.254", id="ipv4-mapped-metadata"),
    pytest.param("64:ff9b::7f00:1", id="nat64-loopback"),
    pytest.param("2002:7f00:1::", id="6to4-loopback"),
    pytest.param("2002:a9fe:a9fe::1", id="6to4-metadata"),
    pytest.param("::7f00:1", id="ipv4-compatible-loopback"),
    pytest.param("::a9fe:a9fe", id="ipv4-compatible-metadata"),
    pytest.param("::808:808", id="ipv4-compatible-public-embedded"),
    pytest.param("64:ff9b:1::1", id="nat64-local-use"),
]


@pytest.mark.parametrize("address", REFUSED_ADDRESSES)
def test_an_internal_address_is_refused_and_never_contacted(address: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    checker = make_checker(handler, FakeResolver({"example.test": [address]}))

    assert refusal_of(checker, "https://example.test/").reason is ReferenceFailure.INTERNAL_ADDRESS
    assert requests == []


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "2001:4860:4860::8888", "8.8.8.8"])
def test_a_public_address_is_accepted(address: str) -> None:
    assert is_public_address(address)


@pytest.mark.parametrize("address", ["not-an-address", "", "999.1.1.1"])
def test_an_unparseable_address_is_not_public(address: str) -> None:
    assert not is_public_address(address)


def test_a_host_with_one_internal_address_among_public_ones_is_refused_whole() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    checker = make_checker(handler, FakeResolver({"example.test": [PUBLIC_V4, "10.0.0.5"]}))

    assert refusal_of(checker, "https://example.test/").reason is ReferenceFailure.INTERNAL_ADDRESS
    assert requests == []


@pytest.mark.parametrize("host", ["localhost", "2130706433", "0x7f.1"])
def test_a_name_that_resolves_to_loopback_is_refused(host: str) -> None:
    checker = make_checker(ok, FakeResolver({host: ["127.0.0.1"]}))

    assert refusal_of(checker, f"http://{host}/").reason is ReferenceFailure.INTERNAL_ADDRESS


# --- the shape of the URL --------------------------------------------------

INVALID_URLS = [
    pytest.param("", id="empty"),
    pytest.param("example.test/paper", id="no-scheme"),
    pytest.param("ftp://example.test/file", id="ftp"),
    pytest.param("file:///etc/passwd", id="file"),
    pytest.param("gopher://example.test/", id="gopher"),
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param("https:///path", id="no-host"),
    pytest.param("https://user:pass@example.test/", id="user-info"),
    pytest.param("https://user@example.test/", id="user-name-only"),
    pytest.param("https://example.test\\@127.0.0.1/", id="backslash-user-info-smuggle"),
    pytest.param("http://example.test:80@127.0.0.1/", id="port-as-user-info-smuggle"),
    pytest.param("https://127.0.0.1/", id="literal-ipv4-loopback"),
    pytest.param("https://93.184.216.34/", id="literal-ipv4-public"),
    pytest.param("http://[::1]/", id="literal-ipv6-loopback"),
    pytest.param("http://[::ffff:127.0.0.1]/", id="literal-ipv4-mapped"),
    pytest.param("https://example.test:8443/", id="port-8443"),
    pytest.param("http://example.test:8080/", id="port-8080"),
    pytest.param("https://example.test:22/", id="port-22"),
    pytest.param("https://example.test:0/", id="port-zero"),
    pytest.param("https://example.test:99999/", id="port-out-of-range"),
    pytest.param("https://example.test:abc/", id="port-not-a-number"),
    pytest.param("https://example.test/a b", id="space"),
    pytest.param("https://example.test/a\r\nHost: evil.test", id="control-characters"),
    pytest.param("https://example.test/" + "a" * 2100, id="too-long"),
    pytest.param("https://☃.example/", id="character-idna-2008-forbids"),
]


@pytest.mark.parametrize("url", INVALID_URLS)
def test_a_malformed_or_disallowed_url_is_refused_before_any_lookup(url: str) -> None:
    resolver = FakeResolver({})
    checker = make_checker(ok, resolver)

    assert refusal_of(checker, url).reason is ReferenceFailure.INVALID_URL
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("url", "port", "host_header"),
    [
        ("http://example.test/", 80, "example.test"),
        ("https://example.test/", 443, "example.test"),
        ("http://example.test:80/", 80, "example.test"),
        ("https://example.test:443/", 443, "example.test"),
        ("https://example.test:80/", 80, "example.test:80"),
        ("http://example.test:443/", 443, "example.test:443"),
        ("HTTPS://Example.TEST/x", 443, "example.test"),
    ],
)
def test_only_ports_80_and_443_are_contacted(url: str, port: int, host_header: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    resolver = FakeResolver({"example.test": [PUBLIC_V4]})
    make_checker(handler, resolver).check(url)

    assert resolver.calls == [("example.test", port)]
    assert seen[0].headers["host"] == host_header
    # httpx drops a port that is the scheme's default, so absence means 80 or 443.
    default = {"http": 80, "https": 443}[seen[0].url.scheme]
    assert (seen[0].url.port or default) == port


def test_an_internationalised_host_name_is_sent_as_ascii() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    resolver = FakeResolver({"xn--bcher-kva.example": [PUBLIC_V4]})
    make_checker(handler, resolver).check("https://bücher.example/")

    assert seen[0].headers["host"] == "xn--bcher-kva.example"
    assert seen[0].extensions["sni_hostname"] == "xn--bcher-kva.example"


def test_a_host_name_is_encoded_as_browsers_encode_it() -> None:
    """IDNA 2008 keeps the sharp s; the standard library's IDNA 2003 codec would fold it to
    `ss` and check a different site."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    resolver = FakeResolver({"xn--strae-oqa.de": [PUBLIC_V4]})
    make_checker(handler, resolver).check("https://straße.de/")

    assert seen[0].headers["host"] == "xn--strae-oqa.de"


# --- pinning ---------------------------------------------------------------


def test_the_request_goes_to_the_validated_address_with_the_original_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    make_checker(handler).check("https://example.test/paper?id=7")

    request = seen[0]
    assert request.url.host == PUBLIC_V4
    assert request.url.path == "/paper"
    assert request.url.query == b"id=7"
    assert request.headers["host"] == "example.test"
    assert request.extensions["sni_hostname"] == "example.test"


def test_an_ipv6_address_is_connected_to_in_bracket_form() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    make_checker(handler, FakeResolver({"example.test": [PUBLIC_V6]})).check(
        "https://example.test/"
    )

    assert seen[0].url.host == PUBLIC_V6


def test_a_dns_answer_that_changes_after_the_check_is_never_asked_for_again() -> None:
    """DNS rebinding: the first answer is public, any second answer is internal."""
    answers = iter([[PUBLIC_V4], ["127.0.0.1"], ["127.0.0.1"]])
    calls: list[str] = []

    def rebinding_resolver(host: str, _port: int) -> Sequence[str]:
        calls.append(host)
        return next(answers)

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    checker = HttpReferenceChecker(
        resolver=rebinding_resolver, transport=httpx.MockTransport(handler), probe=Probe(True)
    )
    checker.check("https://example.test/")

    assert calls == ["example.test"]
    assert [r.url.host for r in seen] == [PUBLIC_V4]


def test_the_next_address_is_tried_when_the_first_cannot_connect() -> None:
    other_public = "93.184.216.35"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == PUBLIC_V6:
            raise httpx.ConnectError("no route")
        return httpx.Response(200)

    resolver = FakeResolver({"example.test": [PUBLIC_V6, other_public]})
    result = make_checker(handler, resolver).check("https://example.test/")

    assert seen == [PUBLIC_V6, other_public]
    assert result.status == 200
    assert len(resolver.calls) == 1


# --- redirects -------------------------------------------------------------


def test_a_redirect_from_a_public_host_to_a_private_address_is_refused() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["host"])
        return httpx.Response(302, headers={"Location": "https://internal.test/admin"})

    resolver = FakeResolver({"example.test": [PUBLIC_V4], "internal.test": ["10.1.2.3"]})
    checker = make_checker(handler, resolver)

    assert refusal_of(checker, "https://example.test/").reason is ReferenceFailure.INTERNAL_ADDRESS
    assert seen == ["example.test"]


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("http://127.0.0.1/", ReferenceFailure.INVALID_URL),
        ("http://169.254.169.254/latest/meta-data/", ReferenceFailure.INVALID_URL),
        ("http://[::1]/", ReferenceFailure.INVALID_URL),
        ("ftp://example.test/file", ReferenceFailure.INVALID_URL),
        ("https://example.test:8443/", ReferenceFailure.INVALID_URL),
        ("https://user:pw@example.test/", ReferenceFailure.INVALID_URL),
        ("//localhost/", ReferenceFailure.INTERNAL_ADDRESS),
    ],
)
def test_every_redirect_target_gets_the_same_checks(
    location: str, reason: ReferenceFailure
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": location})

    resolver = FakeResolver({"example.test": [PUBLIC_V4], "localhost": ["127.0.0.1"]})

    assert refusal_of(make_checker(handler, resolver), "https://example.test/").reason is reason


def test_a_relative_redirect_is_resolved_against_the_current_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/a/start":
            return httpx.Response(302, headers={"Location": "../moved?x=1"})
        return httpx.Response(200)

    result = make_checker(handler).check("https://example.test/a/start")

    assert seen == ["/a/start", "/moved"]
    assert result.status == 200


def test_a_redirect_to_another_public_host_is_followed_and_resolved_again() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.test":
            return httpx.Response(308, headers={"Location": "https://other.test/"})
        return httpx.Response(200)

    resolver = FakeResolver({"example.test": [PUBLIC_V4], "other.test": ["93.184.216.35"]})
    result = make_checker(handler, resolver).check("https://example.test/")

    assert result.status == 200
    assert [host for host, _port in resolver.calls] == ["example.test", "other.test"]


def test_the_redirect_cap_refuses_a_chain_that_never_ends() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(302, headers={"Location": f"https://example.test/{count}"})

    checker = make_checker(handler)

    assert (
        refusal_of(checker, "https://example.test/").reason is ReferenceFailure.TOO_MANY_REDIRECTS
    )
    assert count == MAX_REDIRECTS + 1


def test_a_chain_of_exactly_the_cap_still_passes() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count <= MAX_REDIRECTS:
            return httpx.Response(302, headers={"Location": "https://example.test/next"})
        return httpx.Response(200)

    assert make_checker(handler).check("https://example.test/").status == 200


def test_a_redirect_with_no_location_fails_on_its_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    error = refusal_of(make_checker(handler), "https://example.test/")

    assert (error.reason, error.status) == (ReferenceFailure.BAD_STATUS, 302)


# --- statuses --------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 204, 206, 299, 401, 403])
def test_a_passing_status_is_recorded(status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    result = make_checker(handler).check("https://example.test/")

    assert (result.status, result.checked_at) == (status, FIXED_NOW)


@pytest.mark.parametrize("status", [400, 404, 405, 410, 418, 429, 451, 500, 502, 503, 300, 304])
def test_any_other_status_fails_and_names_itself(status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    error = refusal_of(make_checker(handler), "https://example.test/")

    assert (error.reason, error.status) == (ReferenceFailure.BAD_STATUS, status)


def test_a_redirect_chain_that_ends_in_404_fails_with_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "/gone"})
        return httpx.Response(404)

    error = refusal_of(make_checker(handler), "https://example.test/")

    assert (error.reason, error.status) == (ReferenceFailure.BAD_STATUS, 404)


def test_a_get_is_sent_and_the_body_is_never_read() -> None:
    class TrippedStream(httpx.SyncByteStream):
        read = False
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            TrippedStream.read = True
            yield b"body"

        def close(self) -> None:
            TrippedStream.closed = True

    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, stream=TrippedStream())

    make_checker(handler).check("https://example.test/")

    assert methods == ["GET"]
    assert not TrippedStream.read
    assert TrippedStream.closed


# --- timeouts --------------------------------------------------------------


def test_a_read_timeout_is_a_timeout_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    probe = Probe(True)
    error = refusal_of(make_checker(handler, probe=probe), "https://example.test/")

    assert error.reason is ReferenceFailure.TIMEOUT
    assert probe.calls == 0


def test_the_per_request_timeouts_are_set_from_the_constants() -> None:
    seen: list[dict[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200)

    make_checker(handler).check("https://example.test/")

    assert seen[0]["connect"] == reference_check.CONNECT_TIMEOUT_SECONDS
    assert seen[0]["read"] == reference_check.READ_TIMEOUT_SECONDS


def test_a_slow_chain_stops_at_the_overall_deadline() -> None:
    clock = FakeClock()
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        clock.now += OVERALL_DEADLINE_SECONDS / 2 + 1
        return httpx.Response(302, headers={"Location": "https://example.test/next"})

    checker = make_checker(handler, monotonic=clock)

    assert refusal_of(checker, "https://example.test/").reason is ReferenceFailure.TIMEOUT
    assert count == 2


def test_a_request_timeout_never_exceeds_the_time_left() -> None:
    clock = FakeClock()
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        clock.now += OVERALL_DEADLINE_SECONDS - 1
        return httpx.Response(302, headers={"Location": "https://example.test/next"})

    refusal_of(make_checker(handler, monotonic=clock), "https://example.test/")

    assert seen == [reference_check.READ_TIMEOUT_SECONDS, 1.0]


@pytest.fixture
def short_deadline(monkeypatch: pytest.MonkeyPatch) -> float:
    seconds = 0.3
    monkeypatch.setattr(reference_check, "OVERALL_DEADLINE_SECONDS", seconds)
    return seconds


def _wait_for_workers_to_end() -> None:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if not [t for t in threading.enumerate() if t.name == "reference-check"]:
            return
        time.sleep(0.01)
    raise AssertionError("a reference-check worker thread is still running")


def test_a_server_that_never_finishes_answering_is_cut_off_at_the_deadline(
    short_deadline: float,
) -> None:
    """A server dripping header bytes never trips a per-read timeout, so the limit must be
    wall-clock. The blocked handler stands in for it."""
    release = threading.Event()
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        release.wait(timeout=10)
        return httpx.Response(302, headers={"Location": "https://example.test/next"})

    started = time.monotonic()
    try:
        error = refusal_of(make_checker(handler), "https://example.test/")
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert error.reason is ReferenceFailure.TIMEOUT
    assert elapsed < short_deadline + 2
    _wait_for_workers_to_end()
    assert requests == 1, "the abandoned worker must not follow the redirect"


def test_a_lookup_that_never_returns_is_cut_off_at_the_deadline(short_deadline: float) -> None:
    release = threading.Event()

    def stuck_resolver(_host: str, _port: int) -> Sequence[str]:
        release.wait(timeout=10)
        return [PUBLIC_V4]

    checker = HttpReferenceChecker(
        resolver=stuck_resolver, transport=httpx.MockTransport(ok), probe=Probe(True)
    )

    try:
        error = refusal_of(checker, "https://example.test/")
    finally:
        release.set()

    assert error.reason is ReferenceFailure.TIMEOUT
    _wait_for_workers_to_end()


def test_a_probe_that_never_returns_is_cut_off_at_the_deadline(short_deadline: float) -> None:
    release = threading.Event()

    def stuck_probe() -> bool:
        release.wait(timeout=10)
        return True

    resolver = FakeResolver({"example.test": socket.gaierror("no such host")})
    checker = HttpReferenceChecker(
        resolver=resolver, transport=httpx.MockTransport(ok), probe=stuck_probe
    )

    try:
        error = refusal_of(checker, "https://example.test/")
    finally:
        release.set()

    assert error.reason is ReferenceFailure.TIMEOUT
    _wait_for_workers_to_end()


# --- no outbound access ----------------------------------------------------


def test_a_failed_lookup_with_working_internet_is_a_bad_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = FakeResolver({"example.test": socket.gaierror("no such host")})
    probe = Probe(True)

    with caplog.at_level(logging.DEBUG, logger=reference_check.__name__):
        error = refusal_of(make_checker(ok, resolver, probe=probe), "https://example.test/")

    assert error.reason is ReferenceFailure.NAME_NOT_FOUND
    assert probe.calls == 1
    assert caplog.records == []


def test_a_failed_lookup_with_no_internet_is_unavailable_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = FakeResolver({"example.test": socket.gaierror("no such host")})
    secret_url = "https://example.test/paper?token=s3cret"

    with (
        caplog.at_level(logging.DEBUG, logger=reference_check.__name__),
        pytest.raises(ReferenceCheckUnavailableError),
    ):
        make_checker(ok, resolver, probe=Probe(False)).check(secret_url)

    assert [(r.levelno, r.name) for r in caplog.records] == [
        (logging.WARNING, reference_check.__name__)
    ]
    assert "outbound internet access" in caplog.records[0].getMessage()
    assert "s3cret" not in caplog.text
    assert "example.test" not in caplog.text


def test_an_empty_answer_is_a_failed_lookup() -> None:
    error = refusal_of(
        make_checker(ok, FakeResolver({"example.test": []})), "https://example.test/"
    )

    assert error.reason is ReferenceFailure.NAME_NOT_FOUND


def test_a_refused_connection_with_working_internet_is_a_bad_link() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    probe = Probe(True)
    error = refusal_of(make_checker(handler, probe=probe), "https://example.test/")

    assert error.reason is ReferenceFailure.UNREACHABLE
    assert probe.calls == 1


def test_a_refused_connection_with_no_internet_is_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route")

    with pytest.raises(ReferenceCheckUnavailableError):
        make_checker(handler, probe=Probe(False)).check("https://example.test/")


def test_a_failure_after_connecting_is_not_mistaken_for_no_internet() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("garbled")

    probe = Probe(False)
    error = refusal_of(make_checker(handler, probe=probe), "https://example.test/")

    assert error.reason is ReferenceFailure.UNREACHABLE
    assert probe.calls == 0


def test_the_probe_is_not_run_when_the_check_succeeds() -> None:
    probe = Probe(False)

    make_checker(ok, probe=probe).check("https://example.test/")

    assert probe.calls == 0


# --- the real resolver, probe and transport --------------------------------


@pytest.mark.req("NFR-37")
def test_the_guard_refuses_the_default_transport() -> None:
    checker = HttpReferenceChecker(
        resolver=FakeResolver({"example.test": [PUBLIC_V4]}), probe=Probe(True)
    )

    with pytest.raises(AssertionError, match="NFR-37"):
        checker.check("https://example.test/")


def test_the_system_resolver_returns_each_address_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int, **_kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert (host, port) == ("example.test", 443)
        stream = socket.SOCK_STREAM
        return [
            (socket.AF_INET, stream, 6, "", (PUBLIC_V4, 443)),
            (socket.AF_INET, stream, 6, "", (PUBLIC_V4, 443)),
            (socket.AF_INET6, stream, 6, "", (PUBLIC_V6, 443, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert system_resolver("example.test", 443) == [PUBLIC_V4, PUBLIC_V6]


class _CountingConfig:
    from_env_calls = 0

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    @classmethod
    def make(cls, base_url: str) -> type[_CountingConfig]:
        cls.from_env_calls = 0
        return type("Config", (cls,), {"_base_url": base_url})

    @classmethod
    def from_env(cls) -> _CountingConfig:
        _CountingConfig.from_env_calls += 1
        return _CountingConfig(cls._base_url)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://tx.example.test/fhir", ("tx.example.test", 443)),
        ("http://tx.internal.test/fhir", ("tx.internal.test", 80)),
        ("https://tx.example.test:8443/fhir", ("tx.example.test", 8443)),
    ],
)
def test_the_probe_reads_the_terminology_config_once_and_connects_to_its_host(
    monkeypatch: pytest.MonkeyPatch, base_url: str, expected: tuple[str, int]
) -> None:
    connections: list[tuple[str, int]] = []
    monkeypatch.setattr(dependencies, "TerminologyConfig", _CountingConfig.make(base_url))
    monkeypatch.setattr(
        dependencies, "tcp_probe", lambda host, port: connections.append((host, port)) or True
    )

    probe = dependencies._outbound_probe()
    assert probe() and probe()

    assert _CountingConfig.from_env_calls == 1
    assert connections == [expected, expected]


def test_a_malformed_terminology_setting_fails_when_the_probe_is_built_not_during_a_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_TX_TIMEOUT_SECONDS", "not-a-number")

    with pytest.raises(TerminologyConfigError):
        dependencies._outbound_probe()


class _OpenConnection:
    def __enter__(self) -> _OpenConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_the_tcp_probe_reports_whether_a_connection_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: _OpenConnection())
    assert tcp_probe("tx.example.test", 443)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("unreachable")

    monkeypatch.setattr(socket, "create_connection", refuse)
    assert not tcp_probe("tx.example.test", 443)
