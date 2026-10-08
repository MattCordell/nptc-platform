"""Which address a request is attributed to, behind a proxy or not (FR-22, NFR-24).

Pure functions: no container, no network.
"""

from __future__ import annotations

import ipaddress

import pytest

from nptc.api.client_ip import bucket_key, parse_address, resolve_client_address


def _resolve(peer: str | None, forwarded_for: str | None, *trusted: str) -> str | None:
    address = resolve_client_address(
        peer, forwarded_for, [ipaddress.ip_network(item) for item in trusted]
    )
    return None if address is None else str(address)


@pytest.mark.req("NFR-24")
def test_a_header_from_an_untrusted_peer_is_ignored() -> None:
    """The negative case. A caller who reaches the API directly can write any
    `X-Forwarded-For`; believing it would let them pick their own bucket."""
    assert _resolve("198.51.100.9", "203.0.113.7", "10.0.0.0/8") == "198.51.100.9"


@pytest.mark.req("NFR-24")
def test_no_trusted_proxies_means_the_header_is_never_read() -> None:
    assert _resolve("10.0.0.5", "203.0.113.7") == "10.0.0.5"


@pytest.mark.req("NFR-24")
def test_a_trusted_peer_names_the_caller_in_the_header() -> None:
    assert _resolve("10.0.0.5", "203.0.113.7", "10.0.0.0/8") == "203.0.113.7"


@pytest.mark.req("NFR-24")
def test_a_forged_left_hand_entry_is_never_reached() -> None:
    """The caller wrote `192.0.2.1`; the proxy appended the address it saw. Reading from the
    left would hand the caller a free choice of bucket."""
    assert _resolve("10.0.0.5", "192.0.2.1, 203.0.113.7", "10.0.0.0/8") == "203.0.113.7"


@pytest.mark.req("NFR-24")
def test_trusted_hops_are_skipped_until_the_first_untrusted_address() -> None:
    chain = "203.0.113.7, 10.0.0.9, 10.0.0.8"

    assert _resolve("10.0.0.5", chain, "10.0.0.0/8") == "203.0.113.7"


@pytest.mark.req("NFR-24")
def test_a_hop_that_is_not_an_address_stops_the_walk() -> None:
    assert _resolve("10.0.0.5", "192.0.2.1, garbage, 10.0.0.9", "10.0.0.0/8") == "10.0.0.9"


@pytest.mark.req("NFR-24")
def test_a_chain_of_only_trusted_addresses_resolves_to_its_leftmost() -> None:
    assert _resolve("10.0.0.5", "10.0.0.7, 10.0.0.9", "10.0.0.0/8") == "10.0.0.7"


@pytest.mark.req("NFR-24")
@pytest.mark.parametrize("peer", ["testclient", "/run/nptc.sock", "", None])
def test_a_connecting_address_that_is_not_an_ip_resolves_to_none(peer: str | None) -> None:
    assert _resolve(peer, "203.0.113.7", "10.0.0.0/8") is None


@pytest.mark.req("NFR-24")
def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_address() -> None:
    assert parse_address("::ffff:203.0.113.7") == ipaddress.ip_address("203.0.113.7")


@pytest.mark.req("NFR-24")
def test_every_address_in_one_ipv6_subnet_shares_a_bucket() -> None:
    """A caller rotates through a /64 for free, so a budget per address would be no budget."""
    first = ipaddress.ip_address("2001:db8:1:2::1")
    second = ipaddress.ip_address("2001:db8:1:2:ffff::9")
    other_subnet = ipaddress.ip_address("2001:db8:1:3::1")

    assert bucket_key(first) == bucket_key(second)
    assert bucket_key(first) != bucket_key(other_subnet)


@pytest.mark.req("NFR-24")
def test_ipv4_addresses_each_have_their_own_bucket() -> None:
    assert bucket_key(ipaddress.ip_address("203.0.113.7")) == "203.0.113.7"


@pytest.mark.req("NFR-24")
@pytest.mark.parametrize(
    ("hop", "expected"),
    [
        ("203.0.113.7:51234", "203.0.113.7"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        (" 203.0.113.7 ", "203.0.113.7"),
    ],
)
def test_a_forwarded_hop_may_carry_a_port(hop: str, expected: str) -> None:
    """Some load balancers write the client's port into `X-Forwarded-For`. Refusing such a hop
    would resolve every caller to the proxy and give them all one budget."""
    assert parse_address(hop) == ipaddress.ip_address(expected)


@pytest.mark.req("NFR-24")
@pytest.mark.parametrize(
    "hop", ["203.0.113.7:abc", "garbage:80", "[203.0.113.7]:80", "1.2.3.4:", "[::1"]
)
def test_a_hop_with_a_malformed_port_is_not_an_address(hop: str) -> None:
    assert parse_address(hop) is None


@pytest.mark.req("NFR-24")
def test_a_trusted_proxy_that_writes_ports_still_identifies_the_caller() -> None:
    assert _resolve("10.0.0.5", "203.0.113.7:51234", "10.0.0.0/8") == "203.0.113.7"
