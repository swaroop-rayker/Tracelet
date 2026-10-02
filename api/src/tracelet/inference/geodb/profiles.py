"""``asn_profiles`` -- the registry-artifact detector, precomputed (DATA_MODEL 8.1, F4.AC12(a)).

For each ASN: where do the offline databases put its address space, and how much of it
sits on one single point? A high ``modal_share`` is the signature of a database that has
collapsed a whole ISP onto its registration address -- the Bangalore-recorded-as-
Faridabad failure, B1.

**How it is computed.** Walk every IPv4 network in the installed ASN database, look up
its first address in every installed city database, and weight by the network's size in
addresses. IPv6 is left out on purpose: a single /32 would outweigh an ISP's entire IPv4
space, and the ISPs whose v6 dominates (Jio, Vi) are mobile, where rule (b) already
removes city depth.

**Scope.** Only networks the city databases place in India are counted -- the primary
audience (CLAUDE.md section 1), and what keeps this bounded. An ASN elsewhere simply
has no profile, so rule (a) cannot fire for it; that is recorded, not hidden
(ARCHITECTURE section 3.1).

**Where it runs.** ``python -m tracelet.inference.geodb.profiles`` in a subprocess with a
hard address-space cap, like validation (CLAUDE.md section 5): hundreds of thousands of
lookups in one Python loop is the job that must never share a worker's memory. It prints
one JSON line per ASN; the parent canonicalises and stores them.
"""

from __future__ import annotations

import ipaddress
import json
import resource
import sys
from collections import Counter, defaultdict
from typing import Any, Final

COUNTRY: Final = "IN"
ADDRESS_SPACE_CAP: Final = 1536 * 1024 * 1024
Key = tuple[str | None, str | None, float, float]  # city, admin1, lat, lng


def _english(node: Any) -> str | None:
    if isinstance(node, dict):
        names = node.get("names")
        if isinstance(names, dict) and names.get("en"):
            return str(names["en"])
    return None


def _place(record: Any) -> Key | None:
    if not isinstance(record, dict):
        return None
    if (record.get("country") or {}).get("iso_code") != COUNTRY:
        return None
    location = record.get("location") or {}
    lat, lng = location.get("latitude"), location.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lng, int | float):
        return None
    city = _english(record.get("city"))
    if city is None:
        # A record without a city carries the country's (or state's) centroid, not a
        # placement. Counting it made MaxMind's middle-of-India point the "registry
        # address" of Airtel, Tata and Tikona alike (ERRORS.md E30).
        return None
    subdivisions = record.get("subdivisions") or []
    return (
        city,
        _english(subdivisions[0]) if subdivisions else None,
        round(float(lat), 3),
        round(float(lng), 3),
    )


def compute(asn_path: str, city_paths: list[str]) -> dict[int, dict[str, Any]]:
    import maxminddb  # noqa: PLC0415 - imported after the memory cap is in place

    cities = [maxminddb.open_database(p, maxminddb.MODE_MMAP) for p in city_paths]
    weights: dict[int, Counter[Key]] = defaultdict(Counter)
    orgs: dict[int, str] = {}
    with maxminddb.open_database(asn_path, maxminddb.MODE_MMAP) as asn_db:
        for network, record in asn_db:
            if not isinstance(network, ipaddress.IPv4Network) or not isinstance(record, dict):
                continue
            number = record.get("autonomous_system_number")
            if not isinstance(number, int):
                continue
            first = str(network.network_address)
            for reader in cities:
                key = _place(reader.get(first))
                if key is not None:
                    weights[number][key] += network.num_addresses
                    orgs.setdefault(number, str(record.get("autonomous_system_organization") or ""))
    for reader in cities:
        reader.close()

    profiles: dict[int, dict[str, Any]] = {}
    for number, counter in weights.items():
        total = sum(counter.values())
        (city, admin1, lat, lng), top = counter.most_common(1)[0]
        profiles[number] = {
            "asn": number,
            "org": orgs.get(number) or None,
            "modal_city": city,
            "modal_admin1": admin1,
            "modal_lat": lat,
            "modal_lng": lng,
            "modal_share": round(top / total, 3),
            "addresses": total,
        }
    return profiles


def main(argv: list[str]) -> int:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_CAP, ADDRESS_SPACE_CAP))
    asn_path, city_paths = argv[1], argv[2:]
    for profile in compute(asn_path, city_paths).values():
        print(json.dumps(profile))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
