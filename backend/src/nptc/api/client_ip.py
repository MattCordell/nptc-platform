"""The caller's address, as the platform decides it (FR-22, NFR-08).

Behind Caddy the connecting address is the proxy's, so every caller would share it. The real
address arrives in `X-Forwarded-For`, which any caller can also forge. The header is read
only when the connecting address is one of `ApiSettings.trusted_proxies`, and then from the
right: each hop a trusted proxy appended is skipped, and the first address that is not
trusted is the caller. An address a caller put at the left of the header is never reached.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Final

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: One IPv6 subnet is a single household or site, and a caller can rotate through the
#: addresses inside it for free. Keying on the address would give each rotation a fresh budget.
_IPV6_BUCKET_PREFIX: Final = 64


def parse_address(value: str | None) -> IPAddress | None:
    """`None` for anything that is not an IP: Starlette's `TestClient` reports the host
    `"testclient"`, and a server on a unix socket reports the socket path. A trailing port is
    dropped (`203.0.113.7:51234`, `[2001:db8::1]:443`) because some load balancers write one
    into `X-Forwarded-For`."""
    if value is None:
        return None
    host = _without_port(value.strip())
    if host is None:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def resolve_client_address(
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: Sequence[IPNetwork],
) -> IPAddress | None:
    """The caller's address, or `None` when the connecting address is not an IP at all."""
    connecting = parse_address(peer)
    if connecting is None or not forwarded_for or not _is_trusted(connecting, trusted_proxies):
        return connecting

    caller = connecting
    for hop in reversed(forwarded_for.split(",")):
        address = parse_address(hop)
        if address is None:
            # A hop that is not an address was not written by a proxy that follows the
            # header's format, so nothing to its left can be believed either.
            return caller
        caller = address
        if not _is_trusted(address, trusted_proxies):
            return address
    return caller


def _without_port(text: str) -> str | None:
    """The host part of `text`, or `None` when a port or bracket is malformed. A bare IPv6
    address has several colons and no port, so only one colon, or brackets, mark a port."""
    if text.startswith("["):
        host, closed, rest = text[1:].partition("]")
        if not closed or (rest and not _is_port_suffix(rest)):
            return None
        # Brackets are IPv6 syntax; `[203.0.113.7]` is not an address.
        return host if ":" in host else None
    if text.count(":") == 1:
        host, _, port = text.partition(":")
        return host if _is_port(port) else None
    return text


def _is_port(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _is_port_suffix(text: str) -> bool:
    return text.startswith(":") and _is_port(text[1:])


def bucket_key(address: IPAddress) -> str:
    """The string a rate-limit budget is kept under."""
    if isinstance(address, ipaddress.IPv6Address):
        network = ipaddress.ip_network((address, _IPV6_BUCKET_PREFIX), strict=False)
        return str(network)
    return str(address)


def _is_trusted(address: IPAddress, trusted_proxies: Sequence[IPNetwork]) -> bool:
    return any(address in network for network in trusted_proxies)
