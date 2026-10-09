"""The curated ASN lists behind suppression rules (b) and (c) (F4.AC12, ADR-0011).

A VPN network missing from the hosting list is classified as ordinary broadband: its location
can then be confirmed whenever the visitor's timezone happens to agree, and the visit is not
flagged datacenter (docs/ERRORS.md E77).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracelet.inference.sources import asn_org
from tracelet.inference.sources.asn_org import asn_type
from tracelet.inference.types import AsnInfo

DATA = Path(asn_org.__file__).parents[1] / "data" / "asn_classes.json"


def test_packethub_the_nordvpn_network_is_hosting() -> None:
    """E77: seen on the owner's NordVPN test visit, classified 'broadband'."""
    info = asn_org.classify(147049, "PacketHub S.A.")
    assert info.is_hosting
    assert asn_type(info).value == "hosting"


@pytest.mark.parametrize("org", ["PacketHub S.A.", "PACKETHUB SA"])
def test_another_packethub_network_is_caught_by_its_name(org: str) -> None:
    assert asn_org.classify(64512, org).is_hosting


def test_known_vpn_egress_networks_stay_hosting() -> None:
    for asn in (9009, 212238, 147049):  # M247, Datacamp, PacketHub
        assert asn_org.classify(asn, None).is_hosting, asn


def test_indian_carriers_are_not_mistaken_for_hosting() -> None:
    jio = asn_org.classify(55836, "Reliance Jio Infocomm Limited")
    airtel = asn_org.classify(24560, "Bharti Airtel Ltd., Telemedia Services")
    assert jio.is_mobile and not jio.is_hosting
    assert not airtel.is_hosting and not airtel.is_mobile
    assert asn_type(airtel).value == "broadband"
    assert asn_type(AsnInfo()).value == "unknown"


def test_every_listed_asn_is_a_number() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    for section in ("mobile", "cgnat", "hosting", "families"):
        for key in data[section]:
            assert key.isdigit(), (section, key)
