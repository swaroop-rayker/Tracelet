"""Visitor identity's network part (ADR-0006, ADR-0031): the carrier for an enriched visit on
a mobile network, the prefix everywhere else -- and v1 identifiers unchanged."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import hmac

from tracelet.classify.identity import IdentityInput, canonical, derive

STABLE = b"s" * 32
ROTATING = b"r" * 32
FP = b"f" * 32
DAY = dt.date(2026, 10, 11)
JIO = 55836

PHONE = IdentityInput(
    user_agent=(
        "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Mobile Safari/537.36"
    ),
    headers={"accept-language": "en-IN,en;q=0.9", "sec-ch-ua-mobile": "?1"},
    ip_prefix="49.36.12.0/24",
    enriched=True,
    asn=JIO,
    mobile=True,
    screen_w=412,
    screen_h=915,
    dpr=2.625,
    tz_iana="Asia/Kolkata",
    languages=("en-IN", "en"),
    gpu_renderer="Adreno (TM) 740",
    hashes=(b"canvas", None, b"font", b"webgl"),
)


def _id(inp: IdentityInput) -> bytes:
    identity = derive(inp, pepper_stable=STABLE, pepper_rotating=ROTATING, pepper_fp=FP, day=DAY)
    assert identity.visitor_id is not None
    return identity.visitor_id


def test_an_enriched_phone_on_mobile_data_keeps_its_identity_across_the_carriers_pool() -> None:
    other_pool = dataclasses.replace(PHONE, ip_prefix="157.48.201.0/24")
    assert _id(PHONE) == _id(other_pool)
    identity = derive(PHONE, pepper_stable=STABLE, pepper_rotating=ROTATING, pepper_fp=FP, day=DAY)
    assert identity.carrier_asn == JIO


def test_two_carriers_are_still_two_visitors() -> None:
    assert _id(PHONE) != _id(dataclasses.replace(PHONE, asn=45609))


def test_a_different_phone_on_the_same_carrier_is_a_different_visitor() -> None:
    assert _id(PHONE) != _id(dataclasses.replace(PHONE, hashes=(b"other", None, b"font", b"webgl")))


def test_a_server_only_mobile_visit_keeps_the_prefix() -> None:
    bare = dataclasses.replace(PHONE, enriched=False)
    assert _id(bare) != _id(dataclasses.replace(bare, ip_prefix="157.48.201.0/24"))
    identity = derive(bare, pepper_stable=STABLE, pepper_rotating=ROTATING, pepper_fp=FP, day=DAY)
    assert identity.carrier_asn is None


def test_broadband_and_an_unknown_asn_keep_the_prefix() -> None:
    wifi = dataclasses.replace(PHONE, mobile=False, asn=24560)
    assert _id(wifi) != _id(dataclasses.replace(wifi, ip_prefix="122.172.81.0/24"))
    no_asn = dataclasses.replace(PHONE, asn=None)
    assert _id(no_asn) != _id(dataclasses.replace(no_asn, ip_prefix="157.48.201.0/24"))


def test_a_prefix_keyed_identity_is_exactly_the_v1_one() -> None:
    """Wi-Fi and broadband history is unbroken by ADR-0031."""
    wifi = dataclasses.replace(PHONE, mobile=False, asn=24560)
    v1 = hmac.new(
        STABLE, b"\x00".join([b"visitor.v1", canonical(wifi), b"49.36.12.0/24"]), hashlib.sha256
    ).digest()[:16]
    assert _id(wifi) == v1


def test_the_session_fingerprint_follows_the_same_network_part() -> None:
    first = derive(PHONE, pepper_stable=STABLE, pepper_rotating=ROTATING, pepper_fp=FP, day=DAY)
    second = derive(
        dataclasses.replace(PHONE, ip_prefix="157.48.201.0/24"),
        pepper_stable=STABLE,
        pepper_rotating=ROTATING,
        pepper_fp=FP,
        day=DAY,
    )
    assert first.session_fp == second.session_fp
