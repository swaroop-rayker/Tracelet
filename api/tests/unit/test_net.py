"""Client addresses and the Cloudflare edge (F13.AC6).

A forwarding header is a claim, and only a verified peer may make it. Every test in
the ``resolve_client`` section is one way a visitor might try to choose their own
apparent address -- which would defeat rate limiting, poison geolocation and frame
another network, all with one header.
"""

from __future__ import annotations

import ipaddress

import pytest

from tracelet.net import (
    CLOUDFLARE_RANGES,
    is_cloudflare,
    parse_ip,
    prefix_of,
    resolve_client,
)

# Addresses inside published Cloudflare ranges, and one well outside them.
CF_V4 = "162.158.1.10"  # 162.158.0.0/15
CF_V6 = "2606:4700::1"  # 2606:4700::/32
NOT_CF = "8.8.8.8"
VISITOR = "49.207.12.34"


# ---------------------------------------------------------------------------
# Parsing and prefixes
# ---------------------------------------------------------------------------


def test_an_ipv4_mapped_ipv6_address_folds_to_ipv4() -> None:
    """``::ffff:1.2.3.4`` and ``1.2.3.4`` are the same visitor.

    Left unfolded they would HMAC to two identities and land in two different
    rate-limit buckets.
    """
    assert parse_ip("::ffff:49.207.12.34") == ipaddress.IPv4Address(VISITOR)
    assert prefix_of("::ffff:49.207.12.34") == prefix_of(VISITOR)


@pytest.mark.parametrize("value", [None, "", "not-an-ip", "999.1.1.1", "1.2.3"])
def test_garbage_is_not_an_address(value: str | None) -> None:
    assert parse_ip(value) is None
    assert prefix_of(value) is None


def test_prefixes_are_24_and_48() -> None:
    """The durable network identity (RW-3). Coarse enough to survive a CGNAT
    rebalance or an IPv6 privacy-address rotation."""
    assert prefix_of("49.207.12.34") == "49.207.12.0/24"
    assert prefix_of("2401:4900:1c2e:5a7b::1") == "2401:4900:1c2e::/48"


def test_surrounding_whitespace_is_tolerated() -> None:
    assert parse_ip("  49.207.12.34 ") == ipaddress.IPv4Address(VISITOR)


# ---------------------------------------------------------------------------
# Cloudflare ranges
# ---------------------------------------------------------------------------


def test_published_cloudflare_addresses_are_recognised() -> None:
    assert is_cloudflare(parse_ip(CF_V4))
    assert is_cloudflare(parse_ip(CF_V6))


def test_ordinary_addresses_are_not_cloudflare() -> None:
    assert not is_cloudflare(parse_ip(NOT_CF))
    assert not is_cloudflare(parse_ip(VISITOR))
    assert not is_cloudflare(None)


def test_the_range_list_covers_both_families() -> None:
    """A list that lost its IPv6 half would silently distrust every v6 edge."""
    versions = {network.version for network in CLOUDFLARE_RANGES}
    assert versions == {4, 6}


def test_no_range_is_implausibly_wide() -> None:
    """Widening is the dangerous mistake: a stale range Cloudflare has released means
    trusting a forged header from whoever holds it now."""
    for network in CLOUDFLARE_RANGES:
        limit = 13 if network.version == 4 else 29
        assert network.prefixlen >= limit, f"{network} is wider than any Cloudflare range"


# ---------------------------------------------------------------------------
# resolve_client
# ---------------------------------------------------------------------------


def test_a_verified_edge_is_believed() -> None:
    client = resolve_client(peer=CF_V4, cf_connecting_ip=VISITOR, behind_cloudflare=True)

    assert client.ip == VISITOR
    assert client.edge_verified is True
    assert client.forged_edge_header is False


def test_a_verified_ipv6_edge_is_believed() -> None:
    client = resolve_client(peer=CF_V6, cf_connecting_ip=VISITOR, behind_cloudflare=True)
    assert client.ip == VISITOR
    assert client.edge_verified is True


def test_a_forged_header_from_an_ordinary_peer_is_ignored() -> None:
    """F13.AC6, the case that matters: behind Cloudflare, but this request came
    straight to the origin with a header claiming otherwise."""
    client = resolve_client(peer=NOT_CF, cf_connecting_ip=VISITOR, behind_cloudflare=True)

    assert client.ip == NOT_CF, "the real peer, never the claimed address"
    assert client.edge_verified is False
    assert client.forged_edge_header is True


def test_no_header_is_trusted_in_direct_mode_even_from_a_cloudflare_peer() -> None:
    """A Cloudflare Worker or a WARP user can reach a direct-mode origin from a
    Cloudflare address. That proves nothing when the deployment is not behind it."""
    client = resolve_client(peer=CF_V4, cf_connecting_ip=VISITOR, behind_cloudflare=False)

    assert client.ip == CF_V4
    assert client.edge_verified is False
    assert client.forged_edge_header is True


def test_a_plain_request_uses_the_peer() -> None:
    client = resolve_client(peer=VISITOR, cf_connecting_ip=None, behind_cloudflare=False)

    assert client.ip == VISITOR
    assert client.edge_verified is False
    assert client.forged_edge_header is False


def test_an_unparseable_claim_from_a_real_edge_falls_back_to_the_peer() -> None:
    client = resolve_client(peer=CF_V4, cf_connecting_ip="garbage", behind_cloudflare=True)
    assert client.ip == CF_V4
    assert client.edge_verified is False


def test_the_repr_never_renders_an_address() -> None:
    client = resolve_client(peer=VISITOR, cf_connecting_ip=None, behind_cloudflare=False)
    assert VISITOR not in repr(client)
