"""Classification rules (F5, ADR-0011 amendment).

Two halves, weighted equally on purpose: automation and lies must be caught, and real
people -- especially in-app webview visitors, the largest segment -- must not be. A false
'bot' on an Instagram visitor costs a real alert.
"""

from __future__ import annotations

import dataclasses

import pytest

from tracelet.capture.models import Classification
from tracelet.classify.config import ClassifierConfig
from tracelet.classify.rules import ClassifyInput, Context, Verdict, classify

C = ClassifierConfig()

CHROME_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
CHROME_WIN_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-IN,en;q=0.9",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
ANDROID_CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)
ANDROID_HEADERS = {
    **CHROME_WIN_HEADERS,
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}
INSTAGRAM_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/131.0.6778.39 Mobile Safari/537.36 "
    "Instagram 356.0.0.41.101 Android"
)
INSTAGRAM_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 Instagram 340.0.2.18.104"
)
IOS_SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
WEBKIT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}

PHONE_CLIENT = {
    "screen_w": 412,
    "screen_h": 915,
    "touch_points": 5,
    "cpu_cores": 8,
    "gpu_renderer": "Adreno (TM) 740",
}
DESKTOP_PROBES = {
    "webdriver": False,
    "chromeObject": True,
    "pluginCount": 5,
    "mimeTypeCount": 2,
    "fontCount": 9,
    "outerWidth": 1536,
    "permissionsAnomaly": False,
    "cdpArtefacts": False,
}


def visit(**overrides: object) -> ClassifyInput:
    base = ClassifyInput(
        user_agent=CHROME_WIN_UA,
        headers=dict(CHROME_WIN_HEADERS),
        http_version="HTTP/2",
        tls_version="TLS 1.3",
        enriched=True,
        honeypot_tripped=False,
        probes=dict(DESKTOP_PROBES),
        screen_w=1536,
        screen_h=864,
        touch_points=0,
        cpu_cores=12,
        device_memory_gb=8,
        gpu_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        tz_countries=frozenset({"IN"}),
        country="IN",
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]  # test helper forwards typed fields


def run(inp: ClassifyInput, ctx: Context | None = None) -> Verdict:
    return classify(inp, ctx or Context(), C)


def fired(v: Verdict) -> set[str]:
    return {s.rule_id for s in v.signals}


# ---------------------------------------------------------------------------
# Real people stay human
# ---------------------------------------------------------------------------


def test_a_real_desktop_chrome_is_human_with_nothing_fired() -> None:
    v = run(visit())
    assert v.classification is Classification.HUMAN
    assert fired(v) == set()
    assert (v.bot_score, v.spoof_score) == (0, 0)


def test_a_real_android_chrome_is_human() -> None:
    v = run(
        visit(
            user_agent=ANDROID_CHROME_UA,
            headers=dict(ANDROID_HEADERS),
            probes={},
            device_memory_gb=8,
            **PHONE_CLIENT,
        )
    )
    assert v.classification is Classification.HUMAN, fired(v)


def test_android_desktop_site_mode_is_not_a_platform_lie() -> None:
    """Chrome on Android's "desktop site" sends a Linux UA with an Android hint."""
    desktop_site_ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    headers = {**ANDROID_HEADERS, "sec-ch-ua-mobile": "?0"}
    v = run(visit(user_agent=desktop_site_ua, headers=headers))
    assert "uach.platform_mismatch" not in fired(v)


def test_an_instagram_android_webview_without_desktop_internals_is_human() -> None:
    """No window.chrome, outerWidth 0, no plugins: normal in Android WebView."""
    probes = {
        "webdriver": False,
        "chromeObject": False,
        "pluginCount": 0,
        "outerWidth": 0,
        "fontCount": 3,
    }
    headers = {k: v for k, v in ANDROID_HEADERS.items() if not k.startswith("sec-ch-ua")}
    v = run(
        visit(
            user_agent=INSTAGRAM_ANDROID_UA,
            headers=headers,
            probes=probes,
            device_memory_gb=8,
            **PHONE_CLIENT,
        )
    )
    assert v.classification is Classification.HUMAN, fired(v)


def test_an_ios_instagram_visit_whose_enrichment_never_came_is_still_human() -> None:
    """ADR-0011 amendment item 6: the R5 case -- server evidence only."""
    v = run(
        visit(
            user_agent=INSTAGRAM_IOS_UA,
            headers=dict(WEBKIT_HEADERS),
            enriched=False,
            probes={},
            screen_w=None,
            screen_h=None,
            gpu_renderer=None,
            device_memory_gb=None,
            cpu_cores=None,
            touch_points=None,
        )
    )
    assert v.classification is Classification.HUMAN, fired(v)
    assert v.reason == "server_evidence"


def test_ios_safari_is_human() -> None:
    v = run(
        visit(
            user_agent=IOS_SAFARI_UA,
            headers=dict(WEBKIT_HEADERS),
            probes={},
            screen_w=390,
            screen_h=844,
            touch_points=5,
            cpu_cores=6,
            device_memory_gb=None,
            gpu_renderer="Apple GPU",
        )
    )
    assert v.classification is Classification.HUMAN, fired(v)


# ---------------------------------------------------------------------------
# Automation (done-checks)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"accept": "*/*"},  # curl -A "<Chrome UA>"
        {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate",
            "connection": "keep-alive",
        },  # requests
    ],
    ids=["curl", "python-requests"],
)
def test_an_http_library_wearing_a_chrome_ua_is_caught_by_its_header_set_alone(
    headers: dict[str, str],
) -> None:
    """M4 done-check, as amended by SPEC section 11 row 7: header set, not order."""
    v = run(visit(headers=headers, enriched=False, probes={}, screen_w=None, screen_h=None))
    assert v.classification is Classification.BOT
    assert {"hdr.no_fetch_metadata", "hdr.no_accept_language", "hdr.accept_any_only"} <= fired(v)


def test_an_honest_http_library_is_caught_by_its_ua() -> None:
    v = run(visit(user_agent="python-requests/2.32.3", headers={"accept": "*/*"}, enriched=False))
    assert v.classification is Classification.BOT
    assert "ua.automation_tool" in fired(v)


def test_headless_chrome_names_its_reasons() -> None:
    headless_ua = CHROME_WIN_UA.replace("Chrome/", "HeadlessChrome/")
    probes = {**DESKTOP_PROBES, "webdriver": True, "pluginCount": 0, "chromeObject": False}
    v = run(
        visit(
            user_agent=headless_ua,
            probes=probes,
            gpu_renderer="ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)",
        )
    )
    assert v.classification is Classification.BOT
    assert {"ua.automation_tool", "client.webdriver", "client.software_renderer"} <= fired(v)


def test_a_honeypot_hit_is_automation() -> None:
    v = run(visit(honeypot_tripped=True))
    assert v.classification is Classification.BOT
    assert "client.honeypot" in fired(v)


# ---------------------------------------------------------------------------
# Lies (done-checks)
# ---------------------------------------------------------------------------


def test_a_windows_ua_with_an_apple_gpu_is_spoofed() -> None:
    v = run(visit(gpu_renderer="Apple M2 Pro", device_memory_gb=8))
    assert "xcheck.gpu_os" in fired(v)
    assert v.spoof_score >= C.weight("xcheck.gpu_os")


def test_device_memory_under_an_ios_ua_is_flagged() -> None:
    """F5.AC5: Safari on iOS never exposes navigator.deviceMemory."""
    v = run(
        visit(
            user_agent=IOS_SAFARI_UA,
            headers=dict(WEBKIT_HEADERS),
            probes={},
            screen_w=390,
            screen_h=844,
            touch_points=5,
            gpu_renderer="Apple GPU",
            device_memory_gb=8,
        )
    )
    assert "xcheck.ios_device_memory" in fired(v)
    assert v.classification is Classification.SPOOFED


def test_client_hints_that_contradict_the_ua_are_a_lie() -> None:
    headers = {**CHROME_WIN_HEADERS, "sec-ch-ua-platform": '"macOS"'}
    v = run(visit(headers=headers))
    assert "uach.platform_mismatch" in fired(v)


def test_a_patched_ua_with_unpatched_client_hints_is_a_lie() -> None:
    headers = {**CHROME_WIN_HEADERS, "sec-ch-ua": '"Chromium";v="119", "Not?A_Brand";v="24"'}
    v = run(visit(headers=headers))
    assert "uach.version_mismatch" in fired(v)


def test_the_meta_scanner_is_never_human() -> None:
    """RISKS R21: a JavaScript-executing Meta scanner -- Dalvik FBAN/FB4A UA, 52 cores,
    a 2000x2000 screen, LA timezone -- from Meta's own network."""
    v = run(
        visit(
            user_agent="Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A) [FBAN/FB4A;FBAV/450.0]",
            headers={"accept-language": "ar-EG", "accept": "*/*"},
            screen_w=2000,
            screen_h=2000,
            cpu_cores=52,
            touch_points=0,
            tz_countries=frozenset({"US"}),
            country="US",
            is_hosting=True,
        )
    )
    assert v.classification is not Classification.HUMAN
    assert v.classification is Classification.DATACENTER


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


def test_one_fingerprint_on_three_networks_is_a_proxy() -> None:
    v = run(visit(), Context(fingerprint_asns=3))
    assert v.is_proxy_suspected
    assert "net.fingerprint_across_asns" in fired(v)


def test_many_devices_behind_one_prefix_is_a_gateway_not_a_proxy() -> None:
    """M4 done-check: the regression that would misclassify most Indian mobile traffic."""
    v = run(
        visit(
            user_agent=ANDROID_CHROME_UA,
            headers=dict(ANDROID_HEADERS),
            probes={},
            device_memory_gb=8,
            is_mobile_network=True,
            **PHONE_CLIENT,
        ),
        Context(prefix_fingerprints=250, prefix_visits=40),
    )
    assert not v.is_proxy_suspected
    assert "net.shared_gateway" in fired(v)
    assert v.classification is Classification.HUMAN, "a carrier gateway is people"


def test_a_hosting_network_is_datacenter_and_a_tor_exit_says_so() -> None:
    assert run(visit(is_hosting=True)).classification is Classification.DATACENTER
    tor = run(visit(is_tor=True))
    assert (tor.classification, tor.is_tor, tor.reason) == (
        Classification.DATACENTER,
        True,
        "tor_exit",
    )


def test_a_scanner_is_spam_with_its_name() -> None:
    v = run(visit(user_agent="Mozilla/5.0 zgrab/0.x", headers={}))
    assert v.classification is Classification.SPAM
    assert any(s.detail.get("matched") == "zgrab" for s in v.signals)


def test_an_exploit_probe_recorded_at_capture_is_spam() -> None:
    v = run(visit(capture_signals=("capture.exploit_probe",)))
    assert v.classification is Classification.SPAM


def test_a_preview_fetcher_stays_a_crawler() -> None:
    v = run(
        visit(user_agent="facebookexternalhit/1.1", capture_signals=("ua.link_preview_fetcher",))
    )
    assert v.classification is Classification.CRAWLER


def test_a_burst_from_one_broadband_prefix_is_a_rate_anomaly_but_not_from_a_carrier() -> None:
    assert "net.rate_anomaly" in fired(run(visit(), Context(prefix_visits=25)))
    assert "net.rate_anomaly" not in fired(
        run(visit(is_mobile_network=True), Context(prefix_visits=25))
    )


def test_scores_never_exceed_100() -> None:
    v = run(
        visit(
            honeypot_tripped=True,
            probes={**DESKTOP_PROBES, "webdriver": True, "cdpArtefacts": True},
        )
    )
    assert v.bot_score == 100
