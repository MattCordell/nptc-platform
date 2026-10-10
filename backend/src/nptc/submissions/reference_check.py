"""The supporting-reference check for a new-test submission (FR-27).

The platform fetches a URL a submitter typed, so the check is a server-side
request forgery surface: a URL that resolves, directly or through a redirect, to
an address inside the deployment must never be contacted. Four rules hold it:

- The host is resolved once per hop and every returned address must be public.
  The request then goes to a validated address, not to the name, so a DNS answer
  that changes between check and connect cannot reach an internal host.
- Redirects are followed here, not by httpx, so every hop passes the same checks.
- Only the status is read. The response body is never read, stored or logged.
- No proxy is used (`trust_env=False`): a proxy would resolve the name itself and
  bypass the pin.

`ReferenceChecker` is the seam routes depend on. `HttpReferenceChecker` takes its
resolver, transport and probe as arguments so tests make no network call (NFR-37).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar, Final, Protocol
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

_logger = logging.getLogger(__name__)

MAX_REDIRECTS: Final = 5
CONNECT_TIMEOUT_SECONDS: Final = 5.0
READ_TIMEOUT_SECONDS: Final = 5.0
#: Covers every hop and every address tried, so a slow chain cannot hold a request open.
OVERALL_DEADLINE_SECONDS: Final = 15.0
MAX_URL_LENGTH: Final = 2048

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
_ALLOWED_PORTS: Final = frozenset({80, 443})
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
#: The page exists but wants a sign-in, which a link checker cannot give it.
_PASSING_STATUSES: Final = frozenset({401, 403})
_USER_AGENT: Final = "nptc-reference-check"


class ReferenceFailure(Enum):
    INVALID_URL = "invalid_url"
    INTERNAL_ADDRESS = "internal_address"
    BAD_STATUS = "bad_status"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    TIMEOUT = "timeout"
    NAME_NOT_FOUND = "name_not_found"
    UNREACHABLE = "unreachable"


class ReferenceCheckFailedError(Exception):
    """The link is not usable and the submitter can fix it: a 422.

    `status` is set for `BAD_STATUS` only. The message is diagnostic and carries no URL;
    `nptc.api.errors` builds the response sentence from `reason`.
    """

    http_status: ClassVar[int] = 422

    def __init__(self, reason: ReferenceFailure, *, status: int | None = None) -> None:
        super().__init__(reason.value if status is None else f"{reason.value}: {status}")
        self.reason = reason
        self.status = status


class ReferenceCheckUnavailableError(Exception):
    """The check could not run because this deployment has no outbound internet access.

    This is not the submitter's fault, so it is a 503 and nothing is saved.
    """

    http_status: ClassVar[int] = 503


@dataclass(frozen=True)
class ReferenceCheckResult:
    """`status` is the final response's status, after any redirects."""

    checked_at: datetime
    status: int


class ReferenceChecker(Protocol):
    def check(self, url: str) -> ReferenceCheckResult: ...


#: Host and port in, address strings out. Raises `OSError` when the name does not resolve.
Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def tcp_probe(host: str, port: int, *, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """Whether a TCP connection to `host:port` opens: the no-egress test's one question."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_NAT64_PREFIX: Final = ipaddress.IPv6Network("64:ff9b::/96")


def _unwrap_embedded_ipv4(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An IPv6 address that carries an IPv4 one is judged by the IPv4 address inside it.

    Without this, `::ffff:127.0.0.1` is a "global" IPv6 address that a dual-stack
    socket connects to as loopback.
    """
    if isinstance(address, ipaddress.IPv4Address):
        return address
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def is_public_address(raw: str) -> bool:
    """True only for an address that routes on the public internet.

    `is_global` alone is not enough: it is true for IPv4 multicast.
    """
    try:
        address = _unwrap_embedded_ipv4(ipaddress.ip_address(raw))
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    port: int
    path_and_query: str

    @property
    def host_header(self) -> str:
        default = _DEFAULT_PORTS[self.scheme]
        return self.host if self.port == default else f"{self.host}:{self.port}"


def _parse_target(url: str) -> _Target:
    invalid = ReferenceCheckFailedError(ReferenceFailure.INVALID_URL)
    if len(url) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        raise invalid
    try:
        parts: SplitResult = urlsplit(url)
        port = parts.port
    except ValueError:
        raise invalid from None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        raise invalid
    if parts.username is not None or parts.password is not None:
        raise invalid
    # A literal address is refused whatever it is: no honest reference link needs one, and
    # the browser-style numeric forms (`2130706433`) reach the resolver check below anyway.
    if _is_ip_literal(host):
        raise invalid
    # Headers and the TLS server name must be ASCII; `getaddrinfo` applies the same encoding.
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise invalid from None
    effective_port = _DEFAULT_PORTS[scheme] if port is None else port
    if effective_port not in _ALLOWED_PORTS:
        raise invalid
    path = parts.path or "/"
    return _Target(
        scheme=scheme,
        host=host,
        port=effective_port,
        path_and_query=f"{path}?{parts.query}" if parts.query else path,
    )


def _request_url(target: _Target, address: str) -> httpx.URL:
    literal = f"[{address}]" if ":" in address else address
    return httpx.URL(f"{target.scheme}://{literal}:{target.port}{target.path_and_query}")


class HttpReferenceChecker:
    """Checks a URL by fetching its status, as the module docstring describes.

    `probe` answers "does this deployment have outbound internet access?". It runs only
    after a name or connection failure, because those look the same for a mistyped host
    and for a deployment with no egress.
    """

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        probe: Callable[[], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._probe = probe
        self._clock = clock
        self._monotonic = monotonic

    def check(self, url: str) -> ReferenceCheckResult:
        deadline = self._monotonic() + OVERALL_DEADLINE_SECONDS
        current = url
        with httpx.Client(
            transport=self._transport, follow_redirects=False, trust_env=False
        ) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                status, location = self._fetch_status(client, _parse_target(current), deadline)
                if status in _REDIRECT_STATUSES and location:
                    current = urljoin(current, location)
                    continue
                if 200 <= status < 300 or status in _PASSING_STATUSES:
                    return ReferenceCheckResult(checked_at=self._clock(), status=status)
                raise ReferenceCheckFailedError(ReferenceFailure.BAD_STATUS, status=status)
        raise ReferenceCheckFailedError(ReferenceFailure.TOO_MANY_REDIRECTS)

    def _addresses(self, target: _Target) -> list[str]:
        try:
            addresses = list(self._resolver(target.host, target.port))
        except OSError:
            raise self._unreachable(ReferenceFailure.NAME_NOT_FOUND) from None
        if not addresses:
            raise self._unreachable(ReferenceFailure.NAME_NOT_FOUND)
        # Every address, not just the one used: a host that answers with a mix of public
        # and internal addresses is refused whole rather than filtered.
        if not all(is_public_address(address) for address in addresses):
            raise ReferenceCheckFailedError(ReferenceFailure.INTERNAL_ADDRESS)
        return addresses

    def _unreachable(self, failure: ReferenceFailure) -> Exception:
        if not self._probe():
            _logger.warning(
                "reference check cannot run: this deployment has no outbound internet access"
            )
            return ReferenceCheckUnavailableError("no outbound internet access")
        return ReferenceCheckFailedError(failure)

    def _fetch_status(
        self, client: httpx.Client, target: _Target, deadline: float
    ) -> tuple[int, str | None]:
        for address in self._addresses(target):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ReferenceCheckFailedError(ReferenceFailure.TIMEOUT)
            request = client.build_request(
                "GET",
                _request_url(target, address),
                headers={
                    "Host": target.host_header,
                    "User-Agent": _USER_AGENT,
                    "Accept": "*/*",
                },
                extensions={"sni_hostname": target.host},
                timeout=httpx.Timeout(
                    min(READ_TIMEOUT_SECONDS, remaining),
                    connect=min(CONNECT_TIMEOUT_SECONDS, remaining),
                ),
            )
            try:
                response = client.send(request, stream=True)
            except httpx.ConnectError, httpx.ConnectTimeout:
                continue
            except httpx.TimeoutException:
                raise ReferenceCheckFailedError(ReferenceFailure.TIMEOUT) from None
            except httpx.TransportError:
                raise ReferenceCheckFailedError(ReferenceFailure.UNREACHABLE) from None
            try:
                return response.status_code, response.headers.get("Location")
            finally:
                response.close()
        raise self._unreachable(ReferenceFailure.UNREACHABLE)
