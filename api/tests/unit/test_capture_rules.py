"""Link destinations (F1.AC2) and the enrichment-to-column mapping (F3.AC2, F3.AC5).

The destination rules decide where a visitor can be sent, so each rejection is tested
on its own. The resolver is substituted: these tests are about the rules, and a test
that depended on external DNS would fail for reasons unrelated to them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tracelet.capture import links
from tracelet.capture.models import ConsentState
from tracelet.capture.schemas import EnrichmentPayload
from tracelet.capture.service import enrichment_values
from tracelet.errors import ValidationFailed

OWN_HOST = "tracelet.example.com"


@pytest.fixture
def resolves_to(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, list[str]]]:
    """Map host -> addresses. A host not in the map fails to resolve."""
    table: dict[str, list[str]] = {}

    async def fake(host: str) -> list[str]:
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    monkeypatch.setattr(links, "resolve_host", fake)
    yield table


def _code(exc: ValidationFailed) -> str:
    return exc.errors[0].code


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


async def test_a_public_https_destination_is_accepted(
    resolves_to: dict[str, list[str]],
) -> None:
    resolves_to["example.com"] = ["93.184.216.34"]
    assert (
        await links.validate_destination("https://example.com/page?x=1", own_host=OWN_HOST)
        == "https://example.com/page?x=1"
    )


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://example.com/", "NOT_HTTPS"),
        ("javascript:alert(1)", "NOT_HTTPS"),
        ("ftp://example.com/", "NOT_HTTPS"),
        ("https://user:pass@example.com/", "CREDENTIALS_IN_URL"),
        ("https://user@example.com/", "CREDENTIALS_IN_URL"),
        ("https:///no-host", "NO_HOST"),
        ("https://" + "a" * 2050 + ".com", "TOO_LONG"),
    ],
)
async def test_malformed_destinations_are_refused(
    resolves_to: dict[str, list[str]], url: str, code: str
) -> None:
    del resolves_to
    with pytest.raises(ValidationFailed) as caught:
        await links.validate_destination(url, own_host=OWN_HOST)
    assert _code(caught.value) == code
    assert caught.value.errors[0].field == "destination_url"


async def test_a_destination_on_this_site_is_refused(resolves_to: dict[str, list[str]]) -> None:
    """A link to this deployment's own host sends the visitor back into the capture
    surface -- and a link to another link loops."""
    resolves_to[OWN_HOST] = ["93.184.216.34"]
    with pytest.raises(ValidationFailed) as caught:
        await links.validate_destination(f"https://{OWN_HOST}/r/other", own_host=OWN_HOST)
    assert _code(caught.value) == "SELF_REFERENCE"


async def test_an_unresolvable_host_is_refused(resolves_to: dict[str, list[str]]) -> None:
    del resolves_to
    with pytest.raises(ValidationFailed) as caught:
        await links.validate_destination("https://does-not-exist.invalid/", own_host=OWN_HOST)
    assert _code(caught.value) == "UNRESOLVABLE"


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "127.0.0.1", "192.168.1.1", "169.254.169.254", "::1"]
)
async def test_a_host_resolving_to_a_private_address_is_refused(
    resolves_to: dict[str, list[str]], address: str
) -> None:
    """Visitors must never be sent to an internal host by an owner's mistake."""
    resolves_to["internal.example"] = [address]
    with pytest.raises(ValidationFailed) as caught:
        await links.validate_destination("https://internal.example/", own_host=OWN_HOST)
    assert _code(caught.value) == "NOT_PUBLIC"


async def test_one_private_record_among_public_ones_is_enough_to_refuse(
    resolves_to: dict[str, list[str]],
) -> None:
    """Every address is checked, not the first: split-horizon DNS is the case to catch."""
    resolves_to["split.example"] = ["93.184.216.34", "10.0.0.5"]
    with pytest.raises(ValidationFailed):
        await links.validate_destination("https://split.example/", own_host=OWN_HOST)


# ---------------------------------------------------------------------------
# Enrichment mapping
# ---------------------------------------------------------------------------


def _map(payload: dict[str, object]) -> tuple[dict[str, object], list[dict[str, object]]]:
    return enrichment_values(EnrichmentPayload.model_validate(payload))


def test_coordinates_are_stored_only_with_consent() -> None:
    """F4.AC3. The CHECK constraint forbids it too; here a bad payload is a quiet no-op
    rather than an IntegrityError that would turn into a 500."""
    values, _ = _map({"geolocation": {"state": "denied", "lat": 12.97, "lng": 77.59}})
    assert values["consent_state"] is ConsentState.DENIED
    assert "gps_lat" not in values
    assert "gps_lng" not in values


def test_granted_coordinates_are_stored() -> None:
    values, _ = _map(
        {"geolocation": {"state": "granted", "lat": 12.9716, "lng": 77.5946, "accuracyM": 18}}
    )
    assert values["consent_state"] is ConsentState.GRANTED
    assert str(values["gps_lat"]) == "12.971600"
    assert str(values["gps_accuracy_m"]) == "18.0"


def test_an_unanswered_prompt_is_unavailable_with_its_reason() -> None:
    """Not "denied" -- nobody said no -- and not "not_asked", because it was asked."""
    values, absences = _map({"geolocation": {"state": "timeout"}})
    assert values["consent_state"] is ConsentState.UNAVAILABLE
    assert absences == [
        {
            "rule_id": "client.geolocation_absent",
            "category": "absence",
            "weight": 0,
            "detail": {"reason": "timeout"},
        }
    ]


def test_an_absent_block_writes_nothing() -> None:
    """F3.AC5: missing is NULL, never zero. Nothing is written for a block not sent,
    so the column keeps its NULL."""
    values, absences = _map({})
    assert values == {}
    assert absences == []


def test_a_missing_field_inside_a_block_is_none_not_zero() -> None:
    values, _ = _map({"hardware": {"cores": 8}})
    assert values["cpu_cores"] == 8
    assert values["device_memory_gb"] is None


def test_client_hashes_are_rehashed_to_a_fixed_size() -> None:
    """The client can send anything in these; re-hashing bounds the stored size."""
    values, _ = _map({"hashes": {"canvas": "a" * 100, "webgl": "b"}})
    assert isinstance(values["canvas_hash"], bytes)
    assert len(values["canvas_hash"]) == 16
    assert values["canvas_hash"] != values["webgl_hash"]
    assert values["audio_hash"] is None


@pytest.mark.parametrize("field", ["fieldFilled", "linkClicked"])
def test_either_honeypot_signal_trips_it(field: str) -> None:
    values, _ = _map({"honeypot": {field: True}})
    assert values["honeypot_tripped"] is True


def test_an_untouched_honeypot_writes_nothing() -> None:
    values, _ = _map({"honeypot": {"fieldFilled": False, "linkClicked": False}})
    assert "honeypot_tripped" not in values


@pytest.mark.parametrize(
    "payload",
    [
        {"screen": {"w": 0}},
        {"screen": {"w": 999_999}},
        {"hardware": {"cores": 0}},
        {"locale": {"tzIana": "../../etc/passwd"}},
        {"locale": {"tzOffsetMin": 10_000}},
        {"locale": {"languages": ["en"] * 17}},
        {"geolocation": {"state": "granted", "lat": 91, "lng": 0}},
        {"geolocation": {"state": "maybe"}},
        {"gpu": {"renderer": "x" * 300}},
    ],
)
def test_implausible_claims_are_rejected_at_the_boundary(payload: dict[str, object]) -> None:
    """Generous for any real device, tight for a hostile one."""
    with pytest.raises(ValueError, match="validation error"):
        EnrichmentPayload.model_validate(payload)


def test_unknown_keys_are_ignored_not_rejected() -> None:
    """A newer page talking to an older API must degrade to fewer signals, never to a
    422 that loses all of them."""
    payload = EnrichmentPayload.model_validate({"screen": {"w": 390, "novel": 1}, "future": {}})
    assert payload.screen is not None
    assert payload.screen.w == 390
