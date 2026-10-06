"""Classifier weights and thresholds -- a section of the versioned ``inference_settings``.

ADR-0011 amendment, item 4: one settings history for the whole engine, so a tuning change
to classification has the same immutability, audit row and rollback as one to location.

Every rule's contribution is a weight here, keyed by its ``rule_id``. A rule missing from
a stored version falls back to the default below, so adding a rule never invalidates an
old version (F4.AC14). The numbers are starting values, set from what each signal
proves -- a ``webdriver`` flag is the automation announcing itself; a missing
``Accept-Language`` is merely unusual -- not measurements; M8 re-derives them.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

CLASSIFIER_REVISION: Final = "m4.1"


def classifier_version(settings_version: int) -> str:
    return f"{CLASSIFIER_REVISION}+s{settings_version}"


DEFAULT_WEIGHTS: Final[dict[str, int]] = {
    # --- automation, announced or proven --------------------------------------
    "client.honeypot": 100,
    "client.webdriver": 80,
    "client.cdp_artefacts": 80,
    "ua.automation_tool": 80,
    "ua.headless": 70,
    "client.software_renderer": 60,
    "ua.missing": 50,
    "client.permissions_anomaly": 40,
    "client.zero_outer_width": 40,
    # --- headers that a real browser of the claimed family always sends (F5.AC8) --
    "hdr.no_fetch_metadata": 30,
    "hdr.accept_any_only": 25,
    "hdr.no_accept_language": 20,
    "http.version_1_0": 30,
    "client.chrome_object_missing": 30,
    "client.no_plugins_desktop_chromium": 30,
    "client.few_fonts": 15,
    "net.rate_anomaly": 30,
    # --- contradictions between claims (spoof_score) --------------------------
    "xcheck.ios_device_memory": 70,
    "xcheck.gpu_os": 50,
    "uach.platform_mismatch": 40,
    "xcheck.impossible_travel": 40,
    "uach.version_mismatch": 30,
    "uach.brand_missing": 30,
    "hdr.chromium_without_client_hints": 30,
    "tls.outdated_for_browser": 30,
    "xcheck.screen_device_class": 35,
    "xcheck.cores_device_class": 30,
    "xcheck.touch_mobile": 30,
    "uach.mobile_mismatch": 25,
    "net.fingerprint_across_asns": 30,
    "xcheck.tz_country": 15,
    # --- malicious automation (F5.AC13, classification 'spam') ----------------
    "ua.scanner": 90,
    "capture.exploit_probe": 80,
    "rdns.scanner": 60,
}


class ClassifierConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    weights: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # At or above: the class. Between the human ceiling and these: 'unknown'.
    bot_threshold: int = Field(default=60, ge=1, le=100)
    spoof_threshold: int = Field(default=50, ge=1, le=100)
    spam_threshold: int = Field(default=60, ge=1, le=100)
    # At or below both: may be 'human' (ADR-0011 amendment, items 6 and 7).
    human_max_bot: int = Field(default=25, ge=0, le=100)
    human_max_spoof: int = Field(default=25, ge=0, le=100)
    # F5.AC7: one fingerprint on this many distinct ASNs inside the window is a proxy...
    collision_min_asns: int = Field(default=3, ge=2, le=50)
    collision_window_hours: int = Field(default=24, ge=1, le=24 * 30)
    # ...and this many distinct fingerprints behind one prefix is a shared gateway -- NAT
    # or a carrier -- and explicitly NOT a proxy.
    gateway_min_fingerprints: int = Field(default=10, ge=2, le=10_000)
    # F5.AC11: visits from one prefix inside the window. Mobile carriers put thousands of
    # people behind one /24, so their ceiling is multiplied.
    rate_window_minutes: int = Field(default=10, ge=1, le=1440)
    rate_max_visits: int = Field(default=20, ge=2, le=100_000)
    rate_mobile_multiplier: int = Field(default=5, ge=1, le=100)
    # Faster than this between two located visits of one fingerprint is not travel.
    impossible_travel_kmh: float = Field(default=900.0, gt=0)

    def weight(self, rule_id: str) -> int:
        return self.weights.get(rule_id, DEFAULT_WEIGHTS.get(rule_id, 0))
