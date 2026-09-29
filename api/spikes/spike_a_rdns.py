"""Spike A (docs/RISKS.md R3): rDNS city-code coverage for Indian ISP address space.

Run 2026-09-29; result 2.0 % (RISKS R3). Kept so the measurement can be repeated, not
because anything imports it. Not part of the application image.

Pre-registered before the first lookup, and unchanged since:

* ISPs and ASNs in ``ISPS``; prefixes are RIPEstat announced-prefixes on the day.
* Per ISP, ``N_V4`` random IPv4 addresses, at most one per /24, the /24s drawn uniformly
  over the announced space; plus ``N_V6`` random IPv6 addresses. Seed ``SEED``.
* A ``CITY_TOKENS`` hit counts toward coverage. ``CIRCLE_TOKENS`` (telecom circle or
  state) are reported separately and do **not**.
* Headline: fraction of sampled IPv4 addresses whose PTR bears a city token, pooled over
  ISPs, and per ISP.

**Run it with a working resolver.** Docker Desktop's DNS proxy answers no PTR queries at
all, which reads as 0 % everywhere (docs/ERRORS.md E27). The script refuses to start if a
control lookup of 8.8.8.8 fails::

    docker run --rm --dns 8.8.8.8 --dns 1.1.1.1 -v "$PWD/api:/app" -w /app \\
        --entrypoint python tracelet-api-tools spikes/spike_a_rdns.py sample > result.json
    docker run --rm -v "$PWD/api:/app" -w /app --entrypoint python tracelet-api-tools \\
        spikes/spike_a_rdns.py analyse < result.json
"""

from __future__ import annotations

import ipaddress
import json
import random
import re
import socket
import sys
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Final, TypedDict

import httpx

SEED: Final = 20260929
N_V4: Final = 150
N_V6: Final = 30
CONTROL: Final = ("8.8.8.8", "dns.google")

ISPS: Final[dict[str, list[int]]] = {
    "Airtel": [24560, 45609],
    "Jio": [55836],
    "ACT": [24309, 18209],
    "BSNL": [9829],
    "Vi": [38266, 45271, 55410],
}

CITY_TOKENS: Final = frozenset(
    [
        "bom",
        "mum",
        "mumbai",
        "bombay",
        "thane",
        "navimumbai",
        "del",
        "delhi",
        "newdelhi",
        "ndl",
        "noida",
        "gurgaon",
        "gurugram",
        "ggn",
        "ghaziabad",
        "faridabad",
        "blr",
        "bang",
        "blore",
        "bgl",
        "bangalore",
        "bengaluru",
        "maa",
        "chn",
        "mds",
        "chennai",
        "madras",
        "hyd",
        "hyderabad",
        "secunderabad",
        "ccu",
        "kol",
        "kolkata",
        "calcutta",
        "pnq",
        "pune",
        "amd",
        "ahd",
        "ahmedabad",
        "jai",
        "jaipur",
        "lko",
        "lucknow",
        "cok",
        "kochi",
        "cochin",
        "ekm",
        "ernakulam",
        "cbe",
        "coimbatore",
        "trv",
        "tvm",
        "trivandrum",
        "thiruvananthapuram",
        "ixc",
        "chd",
        "chandigarh",
        "nag",
        "nagpur",
        "idr",
        "indore",
        "bbi",
        "bhubaneswar",
        "gau",
        "guwahati",
        "pat",
        "patna",
        "ixr",
        "ranchi",
        "bho",
        "bhopal",
        "rpr",
        "raipur",
        "vns",
        "varanasi",
        "vga",
        "vijayawada",
        "vtz",
        "vizag",
        "visakhapatnam",
        "stv",
        "surat",
        "brd",
        "baroda",
        "vadodara",
        "ixm",
        "madurai",
        "trz",
        "trichy",
        "tiruchirappalli",
        "ixe",
        "mangalore",
        "mangaluru",
        "mys",
        "mysore",
        "mysuru",
        "hbx",
        "hubli",
        "goi",
        "goa",
        "udr",
        "udaipur",
        "jdh",
        "jodhpur",
        "atq",
        "amritsar",
        "luh",
        "ludhiana",
        "knu",
        "kanpur",
        "agr",
        "agra",
        "ded",
        "dehradun",
        "ixj",
        "jammu",
        "sxr",
        "srinagar",
        "nsk",
        "nashik",
        "raj",
        "rajkot",
    ]
)

CIRCLE_TOKENS: Final = frozenset(
    [
        "kk",
        "ka",
        "karnataka",
        "tn",
        "tamilnadu",
        "ap",
        "andhra",
        "ts",
        "telangana",
        "kl",
        "kerala",
        "mh",
        "maharashtra",
        "gj",
        "gujarat",
        "rj",
        "rajasthan",
        "up",
        "upe",
        "upw",
        "wb",
        "bengal",
        "pb",
        "punjab",
        "hr",
        "haryana",
        "mp",
        "bh",
        "bihar",
        "or",
        "odisha",
        "orissa",
        "as",
        "assam",
        "ne",
        "jk",
        "hp",
        "north",
        "south",
        "east",
        "west",
    ]
)

_SPLIT: Final = re.compile(r"[^a-z0-9]+|(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class Row(TypedDict):
    isp: str
    family: str
    ip: str
    ptr: str | None
    city: list[str]
    circle: list[str]


def tokens(ptr: str) -> list[str]:
    return [t for t in _SPLIT.split(ptr.lower()) if t and not t.isdigit()]


def announced(client: httpx.Client, asn: int) -> list[Network]:
    response = client.get(
        "https://stat.ripe.net/data/announced-prefixes/data.json",
        params={"resource": f"AS{asn}", "sourceapp": "tracelet-spike-a"},
        timeout=60,
    )
    response.raise_for_status()
    return [ipaddress.ip_network(p["prefix"]) for p in response.json()["data"]["prefixes"]]


def sample_v4(nets: Iterable[ipaddress.IPv4Network], n: int, rng: random.Random) -> list[str]:
    # Collapse more-specifics into their aggregates before counting /24s.
    blocks = list(ipaddress.collapse_addresses(x for x in nets if x.prefixlen <= 24))
    weights = [b.num_addresses // 256 for b in blocks]
    chosen: set[int] = set()
    out: list[str] = []
    while len(out) < min(n, sum(weights)):
        net = rng.choices(blocks, weights)[0]
        base = int(net.network_address) + 256 * rng.randrange(net.num_addresses // 256)
        if base in chosen:
            continue
        chosen.add(base)
        out.append(str(ipaddress.IPv4Address(base + rng.randrange(1, 255))))
    return out


def sample_v6(nets: Iterable[ipaddress.IPv6Network], n: int, rng: random.Random) -> list[str]:
    usable = [x for x in nets if x.prefixlen <= 64]
    if not usable:
        return []
    picks = (rng.choice(usable) for _ in range(n))
    return [
        str(ipaddress.IPv6Address(int(p.network_address) + rng.randrange(p.num_addresses)))
        for p in picks
    ]


def ptr(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


def resolve(ips: list[str]) -> dict[str, str | None]:
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {ip: pool.submit(ptr, ip) for ip in ips}
        wait(futures.values(), timeout=5 + len(ips) / 24 * 5)
        return {ip: (f.result() if f.done() else None) for ip, f in futures.items()}


def sample() -> list[Row]:
    if ptr(CONTROL[0]) != CONTROL[1]:
        msg = f"control PTR of {CONTROL[0]} failed: the resolver cannot answer PTR (E27)"
        raise SystemExit(msg)
    rng = random.Random(SEED)  # noqa: S311 - a reproducible sample, not a secret
    rows: list[Row] = []
    with httpx.Client(headers={"User-Agent": "tracelet-spike-a"}) as client:
        for isp, asns in ISPS.items():
            nets = [net for asn in asns for net in announced(client, asn)]
            v4 = [x for x in nets if isinstance(x, ipaddress.IPv4Network)]
            v6 = [x for x in nets if isinstance(x, ipaddress.IPv6Network)]
            print(f"{isp}: {len(v4)} v4 / {len(v6)} v6 prefixes", file=sys.stderr)
            for family, ips in (("v4", sample_v4(v4, N_V4, rng)), ("v6", sample_v6(v6, N_V6, rng))):
                for ip, name in resolve(ips).items():
                    found = tokens(name) if name else []
                    rows.append(
                        Row(
                            isp=isp,
                            family=family,
                            ip=ip,
                            ptr=name,
                            city=sorted({t for t in found if t in CITY_TOKENS}),
                            circle=sorted({t for t in found if t in CIRCLE_TOKENS}),
                        )
                    )
    return rows


def _line(rows: list[Row]) -> str:
    n = len(rows)
    has_ptr = sum(1 for r in rows if r["ptr"])
    city = sum(1 for r in rows if r["city"])
    circle = sum(1 for r in rows if r["circle"] and not r["city"])

    def pct(k: int) -> str:
        return f"{100 * k / max(n, 1):5.1f}%"

    return (
        f"n={n:4d} ptr={has_ptr:4d} ({pct(has_ptr)}) city={city:4d} ({pct(city)}) "
        f"circle-only={circle:3d} ({pct(circle)})"
    )


def analyse(rows: list[Row]) -> None:
    for family in ("v4", "v6"):
        print(f"== {family}")
        for isp in ISPS:
            print(
                f"  {isp:7s}", _line([r for r in rows if r["isp"] == isp and r["family"] == family])
            )
        print("  POOLED ", _line([r for r in rows if r["family"] == family]))
    print("== city tokens:", Counter(t for r in rows for t in r["city"]).most_common(25))
    print("== circle tokens:", Counter(t for r in rows for t in r["circle"]).most_common(25))


def main(argv: list[str]) -> None:
    command = argv[1] if len(argv) > 1 else ""
    if command == "sample":
        json.dump(sample(), sys.stdout, indent=1)
    elif command == "analyse":
        analyse(json.load(sys.stdin))
    else:
        raise SystemExit("usage: spike_a_rdns.py sample|analyse")


if __name__ == "__main__":
    main(sys.argv)
