"""Which mobile carrier an address belongs to, for the enrichment nonce (ADR-0032).

A carrier's CGNAT can send a page's follow-up request from another /24. The enrichment
nonce is then accepted only if both addresses resolve to the same ASN and that ASN is a
mobile or CGNAT carrier in the curated classification ``connection_class`` already uses.
No ASN database installed means no carrier, and the strict prefix binding applies.
"""

from __future__ import annotations

from tracelet.config import Settings
from tracelet.inference.geodb import readers
from tracelet.inference.sources.asn_org import classify
from tracelet.net import parse_ip


def carrier_of(settings: Settings, ip: str | None) -> int | None:
    """The ASN of the mobile or CGNAT carrier ``ip`` belongs to, else None."""
    address = parse_ip(ip)
    lookup = readers.asn_lookup(settings)
    if address is None or lookup is None:
        return None
    number, org = lookup(address)
    info = classify(number, org)
    return number if (info.is_mobile or info.is_cgnat) else None
