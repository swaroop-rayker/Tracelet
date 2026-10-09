"""Client addresses: which one to believe, and the forms it may be stored in.

Shared by the capture path and the admin API, which is why it lives here rather than
in either. Two rules run through it.

**A forwarding header is a claim, and only a verified peer may make it** (F13.AC6).
``CF-Connecting-IP`` is trusted only when the deployment is behind Cloudflare *and* the
TCP peer is inside a published Cloudflare range. Otherwise any visitor could choose
their own apparent address, and with it defeat rate limiting, poison geolocation, and
frame another network for their traffic -- all at once, with one header.

The same gate covers ``CF-IPCountry`` and ``CF-Ray``. They are geolocation signals
(S8), and an unverified one is attacker-chosen data about location, which is worse
than no data.

**The peer is what Caddy saw.** Caddy writes ``X-Tracelet-Peer-IP`` from
``{remote_host}``, overwriting anything the client sent, so it cannot be forged
(Caddyfile). The API has no published port, so every request arrives through Caddy.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# ---------------------------------------------------------------------------
# Cloudflare edge ranges
# ---------------------------------------------------------------------------
#
# Published at https://www.cloudflare.com/ips-v4 and /ips-v6, retrieved 2026-09-28.
# Cloudflare changes these rarely and announces it in advance, so a pinned list is
# preferred to fetching at boot: a network dependency on the startup path would make
# the capture surface's availability hostage to cloudflare.com's, and the redirect must
# never fail (CLAUDE.md invariant 1).
#
# If Cloudflare adds a range and this list is not updated, the failure mode is SAFE:
# requests from the new range are attributed to the Cloudflare edge rather than to a
# visitor address an attacker could have forged. Geolocation degrades; nothing is
# trusted that should not be. The reverse mistake -- a stale range Cloudflare has since
# released -- is the one that matters, which is why nothing here is ever widened.

CLOUDFLARE_RANGES: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)


def parse_ip(value: str | None) -> IPAddress | None:
    """Parse an address, folding IPv4-mapped IPv6 back to IPv4.

    ``::ffff:203.0.113.7`` and ``203.0.113.7`` are the same visitor. Left unfolded,
    they would HMAC to two identities and fall into two different rate-limit buckets.
    """
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_cloudflare(addr: IPAddress | None) -> bool:
    return addr is not None and any(addr in network for network in CLOUDFLARE_RANGES)


def prefix_of(ip: str | None) -> str | None:
    """Coarsen an address to /24 (v4) or /48 (v6).

    The durable network identity (RW-3, ADR-0007). Coarse enough that a CGNAT
    rebalance or an IPv6 privacy-address rotation does not look like a different
    network, fine enough to tell one network from another.
    """
    addr = parse_ip(ip)
    if addr is None:
        return None
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


# ---------------------------------------------------------------------------
# Resolving the client
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClientAddress:
    """The address this request is attributed to, and how that was decided.

    ``edge_verified`` is the gate for every Cloudflare-supplied header, not only the
    address: ``CF-IPCountry`` and ``CF-Ray`` are believed only when it is true.

    ``forged_edge_header`` records a request that *claimed* to come through Cloudflare
    and did not. It is not an error -- the request is simply attributed to its real
    peer -- but it is evidence, and the classifier (M4) will want it.
    """

    ip: str | None
    edge_verified: bool
    forged_edge_header: bool
    # The address came from the development tunnel's CF-Connecting-IP (ADR-0025).
    via_dev_tunnel: bool = False

    def __repr__(self) -> str:
        # Never render the address itself.
        return (
            f"ClientAddress(ip=<{'set' if self.ip else 'none'}>, "
            f"edge_verified={self.edge_verified}, forged={self.forged_edge_header}, "
            f"tunnel={self.via_dev_tunnel})"
        )


def resolve_client(
    *,
    peer: str | None,
    cf_connecting_ip: str | None,
    behind_cloudflare: bool,
    trusted_tunnel: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None,
) -> ClientAddress:
    """Decide which address a request belongs to (F13.AC6).

    ``trusted_tunnel`` is the development-only exception (ADR-0025): a peer inside that
    network -- the cloudflared container's own -- is believed for the address, and only
    the address: ``edge_verified`` stays false, so CF-Ray and CF-IPCountry are not.
    """
    peer_addr = parse_ip(peer)
    claimed = parse_ip(cf_connecting_ip)

    if behind_cloudflare and claimed is not None and is_cloudflare(peer_addr):
        return ClientAddress(ip=str(claimed), edge_verified=True, forged_edge_header=False)

    if (
        trusted_tunnel is not None
        and claimed is not None
        and peer_addr is not None
        and peer_addr.version == trusted_tunnel.version
        and peer_addr in trusted_tunnel
    ):
        return ClientAddress(
            ip=str(claimed), edge_verified=False, forged_edge_header=False, via_dev_tunnel=True
        )

    return ClientAddress(
        ip=str(peer_addr) if peer_addr is not None else None,
        edge_verified=False,
        # A CF header arriving from anywhere but a verified Cloudflare peer. In direct
        # mode that is every CF header, which is exactly right: nothing legitimate
        # sends one there.
        forged_edge_header=cf_connecting_ip is not None,
    )
