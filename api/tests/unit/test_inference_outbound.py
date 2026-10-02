"""Circuit breaker, S9 parsing and the Nominatim cache (F4.AC7, F11.AC7)."""

from __future__ import annotations

from tracelet.config import Settings
from tracelet.inference import nominatim
from tracelet.inference.outbound import Breaker
from tracelet.inference.sources import external
from tracelet.inference.types import GeoLevel


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_breaker_opens_after_consecutive_failures_and_retries_after_cooldown() -> None:
    clock = Clock()
    b = Breaker("t", threshold=3, cooldown_s=60, _clock=clock)
    for _ in range(3):
        assert b.allow()
        b.failure()
    assert b.state == "open" and not b.allow()

    clock.now += 61
    assert b.state == "half_open" and b.allow(), "one trial call after the cooldown"
    b.failure()
    assert b.state == "open", "a failed trial re-opens at once"

    clock.now += 61
    b.success()
    assert b.state == "closed"


def test_a_success_resets_the_count() -> None:
    b = Breaker("t", threshold=3)
    b.failure()
    b.failure()
    b.success()
    b.failure()
    assert b.state == "closed"


def test_an_ipwhois_answer_becomes_a_candidate() -> None:
    (c,) = external.candidate(
        {
            "success": True,
            "country_code": "in",
            "region": "Karnataka",
            "city": "Bengaluru",
            "latitude": 12.97,
            "longitude": 77.59,
        }
    )
    assert (c.level, c.country_code, c.admin1, c.city) == (
        GeoLevel.CITY,
        "IN",
        "Karnataka",
        "Bengaluru",
    )


def test_an_unsuccessful_ipwhois_answer_is_no_candidate() -> None:
    assert external.candidate({"success": False, "message": "Reserved range"}) == []


def test_nominatim_identifies_the_application() -> None:
    settings = Settings(site_address="links.example.in", acme_email="ops@example.in")
    assert (
        nominatim.user_agent(settings) == "Tracelet/0.1 (+https://links.example.in; ops@example.in)"
    )


def test_nominatim_cache_is_per_rounded_point_and_bounded() -> None:
    nominatim.reset_for_tests()
    nominatim.remember(12.97501, 77.60001, "MG Road")
    assert nominatim.cached(12.97504, 77.59998) == (True, "MG Road"), "~11 m apart is one query"
    for i in range(nominatim.CACHE_SIZE + 5):
        nominatim.remember(10 + i / 100, 70.0, None)
    assert nominatim.cached(12.97501, 77.60001) == (False, None), "evicted, oldest first"
    nominatim.reset_for_tests()
