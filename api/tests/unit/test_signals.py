"""Server-side signal extraction -- and the privacy boundary it enforces (F3.AC1).

``request_headers`` is the one column that could smuggle a plaintext IP into the
database, so most of this file is about what must NOT survive sanitisation. The
version-string tests are docs/ERRORS.md E23: a mask that matches IPv4 by syntax alone
wiped Chrome's version from every Chrome visit, because ``131.0.0.0`` is a valid
address.
"""

from __future__ import annotations

import pytest

from tracelet.capture.signals import (
    IP_MASK,
    MAX_HEADERS,
    MAX_VALUE_LEN,
    clean_referer,
    clean_user_agent,
    edge_signals,
    mask_addresses,
    mask_client,
    normalise_http_version,
    normalise_tls_version,
    sanitise_headers,
    utm_from,
)

CLIENT = "49.207.12.34"
CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)


def _stored(headers: list[tuple[str, str]], client_ip: str | None = CLIENT) -> dict[str, str]:
    return sanitise_headers(headers, client_ip=client_ip)


# ---------------------------------------------------------------------------
# Dropped by name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "X-Forwarded-For",
        "X-Real-IP",
        "Forwarded",
        "True-Client-IP",
        "X-Client-IP",
        "X-Cluster-Client-IP",
        "Fastly-Client-IP",
        "X-Originating-IP",
        "Via",
        "CF-Connecting-IP",
        "CF-Connecting-IPv6",
        "CF-IPCountry",
        "CF-Ray",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Tracelet-Peer-IP",
        "X-Tracelet-TLS",
    ],
)
def test_address_bearing_and_internal_headers_are_dropped(name: str) -> None:
    """Every one either carries a client address by protocol, or was set by our own
    edge rather than the visitor. Dropped whatever the value."""
    assert name.lower() not in _stored([(name, "value")])


@pytest.mark.parametrize("name", ["Cookie", "Authorization", "Proxy-Authorization"])
def test_credentials_are_dropped(name: str) -> None:
    """The capture surface sets no cookie, so any that arrive belong to someone else's
    session on a shared domain."""
    assert name.lower() not in _stored([(name, "secret")])


@pytest.mark.parametrize("name", ["Connection", "Keep-Alive", "Transfer-Encoding", "Upgrade"])
def test_hop_by_hop_headers_are_dropped(name: str) -> None:
    assert name.lower() not in _stored([(name, "close")])


# ---------------------------------------------------------------------------
# Masked by value
# ---------------------------------------------------------------------------


def test_the_observed_address_is_masked_wherever_it_appears() -> None:
    """Layer 1: the invariant-4 guarantee, with no false positives."""
    stored = _stored([("X-Custom", f"client={CLIENT}"), ("User-Agent", f"Bot {CLIENT}")])
    assert CLIENT not in str(stored)
    assert stored["x-custom"] == f"client={IP_MASK}"
    assert stored["user-agent"] == f"Bot {IP_MASK}"


def test_an_unlisted_proxy_header_carrying_another_address_is_masked() -> None:
    """Layer 3: defence in depth against a proxy nobody thought to list."""
    stored = _stored([("X-Some-Proxy-Client", "203.0.113.9")])
    assert stored["x-some-proxy-client"] == IP_MASK


def test_ipv6_addresses_are_masked_too() -> None:
    stored = _stored([("X-Some-Proxy-Client", "2401:4900:1c2e:5a7b::1")])
    assert stored["x-some-proxy-client"] == IP_MASK


def test_the_exact_mask_does_not_match_inside_a_longer_address() -> None:
    """``1.2.3.4`` must not be found inside ``11.2.3.45``."""
    assert mask_client("11.2.3.45", "1.2.3.4") == "11.2.3.45"
    assert mask_client("at 1.2.3.4.", "1.2.3.4") == f"at {IP_MASK}."


# ---------------------------------------------------------------------------
# E23: version numbers that are syntactically addresses
# ---------------------------------------------------------------------------


def test_chromes_version_survives_in_the_stored_headers() -> None:
    """docs/ERRORS.md E23. ``131.0.0.0`` is a valid IPv4 address; the version-bearing
    headers are exempt from the syntactic mask."""
    stored = _stored([("User-Agent", CHROME_UA)])
    assert "Chrome/131.0.0.0" in stored["user-agent"]


def test_chromes_version_survives_in_the_user_agent_column() -> None:
    assert "Chrome/131.0.0.0" in (clean_user_agent(CHROME_UA, client_ip=CLIENT) or "")


@pytest.mark.parametrize(
    "name",
    ["sec-ch-ua", "sec-ch-ua-full-version-list", "sec-ch-ua-platform-version"],
)
def test_client_hint_versions_survive(name: str) -> None:
    value = '"Chromium";v="131.0.0.0", "Google Chrome";v="131.0.0.0"'
    assert _stored([(name, value)])[name] == value


def test_the_observed_address_is_still_masked_in_a_version_bearing_header() -> None:
    """The exemption is from the syntactic mask only, never from the exact one."""
    stored = _stored([("User-Agent", f"{CHROME_UA} {CLIENT}")])
    assert CLIENT not in stored["user-agent"]
    assert "Chrome/131.0.0.0" in stored["user-agent"]


def test_the_syntactic_mask_keeps_what_is_not_an_address() -> None:
    """Times and ordinary version strings parse as non-addresses and survive."""
    assert mask_addresses("12:30:45") == "12:30:45"
    assert mask_addresses("v1.2.3") == "v1.2.3"
    assert mask_addresses("999.1.1.1") == "999.1.1.1"


# ---------------------------------------------------------------------------
# Bounds and shape
# ---------------------------------------------------------------------------


def test_names_are_lowercased_and_repeats_are_joined() -> None:
    stored = _stored([("Accept-Language", "en-IN"), ("accept-language", "en")])
    assert stored == {"accept-language": "en-IN, en"}


def test_values_are_bounded() -> None:
    stored = _stored([("X-Long", "a" * (MAX_VALUE_LEN * 3))])
    assert len(stored["x-long"]) == MAX_VALUE_LEN


def test_the_number_of_headers_is_bounded() -> None:
    """So a hostile client cannot turn one visit into a large row."""
    stored = _stored([(f"X-H{i}", "v") for i in range(MAX_HEADERS * 2)])
    assert len(stored) == MAX_HEADERS


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------


def test_utm_parameters_are_extracted() -> None:
    assert utm_from(
        {"utm_source": "ig", "utm_campaign": "bio", "other": "x"}, client_ip=CLIENT
    ) == {"utm_source": "ig", "utm_campaign": "bio"}


def test_no_utm_parameters_is_none_not_empty() -> None:
    assert utm_from({"q": "x"}, client_ip=CLIENT) is None


def test_an_address_in_a_referer_is_masked() -> None:
    assert clean_referer(f"https://{CLIENT}/page", client_ip=CLIENT) == f"https://{IP_MASK}/page"
    assert clean_referer(None, client_ip=CLIENT) is None


# ---------------------------------------------------------------------------
# Cloudflare edge signals
# ---------------------------------------------------------------------------


def test_edge_signals_are_read_from_a_verified_edge() -> None:
    edge = edge_signals(cf_ray="8b1c2d3e4f5a6b7c-BLR", cf_ipcountry="IN", edge_verified=True)
    assert (edge.cf_colo, edge.cf_country) == ("BLR", "IN")


def test_edge_signals_are_ignored_from_an_unverified_peer() -> None:
    """An unverified CF-IPCountry is a visitor choosing their own country -- attacker-
    chosen location data that would look like an observation."""
    edge = edge_signals(cf_ray="8b1c2d3e4f5a6b7c-BLR", cf_ipcountry="US", edge_verified=False)
    assert (edge.cf_colo, edge.cf_country) == (None, None)


@pytest.mark.parametrize("country", ["XX", "T1", "USA", "in1", ""])
def test_non_countries_are_not_recorded_as_countries(country: str) -> None:
    """XX is Cloudflare's "unknown" and T1 is Tor. Neither is a place."""
    assert edge_signals(cf_ray=None, cf_ipcountry=country, edge_verified=True).cf_country is None


def test_a_malformed_ray_yields_no_colo() -> None:
    assert edge_signals(cf_ray="garbage", cf_ipcountry=None, edge_verified=True).cf_colo is None


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("HTTP/2.0", "HTTP/2"), ("HTTP/3.0", "HTTP/3"), ("HTTP/1.1", "HTTP/1.1"), (None, None)],
)
def test_http_version(raw: str | None, expected: str | None) -> None:
    assert normalise_http_version(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("tls1.3", "TLS 1.3"), ("tls1.2", "TLS 1.2"), ("", None), (None, None), ("ssl3", None)],
)
def test_tls_version(raw: str | None, expected: str | None) -> None:
    """Empty on a plain-HTTP request, since Caddy's placeholder has nothing to say."""
    assert normalise_tls_version(raw) == expected
