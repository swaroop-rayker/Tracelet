"""The classification rules and the decision (F5, ADR-0011 and its M4 amendment).

A pure function: what is known about one visit in, fired rules and a class out. No I/O --
the facts that need the database (collisions, rates, the previous located visit) arrive
already counted in ``Context``.

**Every rule states what it proves.** Weights live in versioned configuration
(``ClassifierConfig``); a fired rule is stored with its weight and its evidence, so no
verdict is unexplainable (F5.AC2).

**In-app webviews are this project's largest human segment** (useragent module
docstring), and several classic headless signals are also normal there -- Android
WebView has no ``window.chrome`` and can report ``outerWidth`` 0. Rules that depend on a
desktop browser's internals never fire on a webview, and the header-set rules are
conservative about them. A false 'bot' on an Instagram visitor costs a real alert.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any, Final

from tracelet.capture.models import Classification, DeviceClass
from tracelet.capture.useragent import ParsedAgent, parse
from tracelet.classify.config import ClassifierConfig

CHROMIUM: Final = frozenset({"Chrome", "Edge", "Opera", "Samsung Internet", "Brave", "Yandex"})
DESKTOP_OS: Final = frozenset({"Windows", "macOS", "Linux", "ChromeOS"})
SOFTWARE_RENDERERS: Final = re.compile(r"swiftshader|llvmpipe|softpipe|mesa offscreen", re.I)
APPLE_GPU: Final = re.compile(r"\bapple\b", re.I)
MOBILE_GPU: Final = re.compile(r"adreno|mali|powervr|immortalis|xclipse", re.I)
DIRECT3D: Final = re.compile(r"direct3d|d3d1[01]", re.I)

# Categories, so the dashboard can group and the decision can sum.
BOT, SPOOF, SPAM, NETWORK, CRAWLER = "bot", "spoof", "spam", "network", "crawler"


@dataclass(frozen=True, slots=True)
class Signal:
    rule_id: str
    category: str
    weight: int
    detail: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "weight": self.weight,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ClassifyInput:
    """The visit, as the classifier may read it."""

    user_agent: str | None
    headers: dict[str, str]
    http_version: str | None
    tls_version: str | None
    enriched: bool
    honeypot_tripped: bool
    capture_signals: tuple[str, ...] = ()  # rule_ids recorded at capture time
    probes: dict[str, Any] = field(default_factory=dict)
    screen_w: int | None = None
    screen_h: int | None = None
    touch_points: int | None = None
    cpu_cores: int | None = None
    device_memory_gb: float | None = None
    gpu_renderer: str | None = None
    tz_countries: frozenset[str] | None = None
    country: str | None = None  # advisory country from inference
    is_hosting: bool = False
    is_mobile_network: bool = False
    is_tor: bool = False
    is_vpn_org: bool = False
    rdns_ptr: str | None = None  # masked


@dataclass(frozen=True, slots=True)
class Context:
    """What the database knew when this visit was classified."""

    fingerprint_asns: int = 0  # distinct ASNs for this fingerprint in the window, incl. this
    prefix_fingerprints: int = 0  # distinct fingerprints behind this prefix in the window
    prefix_visits: int = 0  # visits from this prefix in the rate window, incl. this
    travel_kmh: float | None = None  # speed implied by the previous located visit


@dataclass(frozen=True, slots=True)
class Verdict:
    classification: Classification
    bot_score: int
    spoof_score: int
    signals: tuple[Signal, ...]
    is_datacenter: bool
    is_vpn_suspected: bool
    is_tor: bool
    is_proxy_suspected: bool
    reason: str


@cache
def lists() -> dict[str, tuple[str, ...]]:
    raw: dict[str, Any] = json.loads(
        resources.files("tracelet.classify.data").joinpath("agents.json").read_text()
    )
    return {k: tuple(v) for k, v in raw.items() if isinstance(v, list)}


def _major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.split(".")[0]
    return int(head) if head.isdigit() else None


def _hint(value: str | None) -> str | None:
    return value.strip().strip('"') if value else None


def _brands(sec_ch_ua: str) -> dict[str, int | None]:
    """``"Chromium";v="131", "Not_A Brand";v="24"`` -> ``{"Chromium": 131, ...}``."""
    found: dict[str, int | None] = {}
    for brand, version in re.findall(r'"([^"]+)"\s*;\s*v="(\d+)', sec_ch_ua):
        found[brand] = int(version)
    return found


def _ios_at_least(agent: ParsedAgent, major: int, minor: int) -> bool:
    try:
        parts = [int(p) for p in (agent.os_version or "").split(".")[:2]]
    except ValueError:
        return False
    return len(parts) >= 1 and (parts[0], parts[1] if len(parts) > 1 else 0) >= (major, minor)


# ---------------------------------------------------------------------------
# Rule families
# ---------------------------------------------------------------------------


def _ua_rules(inp: ClassifyInput, w: ClassifierConfig) -> list[Signal]:
    ua = (inp.user_agent or "").lower()
    if not ua:
        return [Signal("ua.missing", BOT, w.weight("ua.missing"))]
    out: list[Signal] = []
    if tool := next((t for t in lists()["scanners"] if t in ua), None):
        out.append(Signal("ua.scanner", SPAM, w.weight("ua.scanner"), {"matched": tool}))
    if tool := next((t for t in lists()["automation_tools"] if t in ua), None):
        out.append(
            Signal("ua.automation_tool", BOT, w.weight("ua.automation_tool"), {"matched": tool})
        )
    elif any(m in ua for m in lists()["headless_markers"]):
        out.append(Signal("ua.headless", BOT, w.weight("ua.headless")))
    return out


def _modern(agent: ParsedAgent) -> bool:
    """Does the claimed browser always send Fetch Metadata on a navigation?"""
    major = _major(agent.ua_version)
    if agent.ua_family in CHROMIUM and agent.os_family != "iOS":
        return major is not None and major >= 80
    if agent.ua_family == "Firefox" and agent.os_family != "iOS":
        return major is not None and major >= 90
    if agent.ua_family == "Safari" or agent.os_family == "iOS":
        # WebKit, so every iOS browser and webview: Fetch Metadata since 16.4.
        return (agent.os_family == "iOS" and _ios_at_least(agent, 16, 4)) or (
            agent.ua_family == "Safari" and major is not None and (major, 0) >= (16, 4)
        )
    return False


def _header_rules(inp: ClassifyInput, agent: ParsedAgent, w: ClassifierConfig) -> list[Signal]:
    """F5.AC8 as amended: the header *set* against what the claimed browser sends."""
    h = inp.headers
    out: list[Signal] = []
    browserlike = agent.ua_family is not None and agent.crawler is None
    if browserlike and _modern(agent) and "sec-fetch-mode" not in h:
        weight = w.weight("hdr.no_fetch_metadata")
        # Halved, not skipped, for webviews: their header behaviour is less studied.
        out.append(
            Signal("hdr.no_fetch_metadata", BOT, weight // 2 if agent.is_inapp_webview else weight)
        )
    if browserlike and "accept-language" not in h:
        out.append(Signal("hdr.no_accept_language", BOT, w.weight("hdr.no_accept_language")))
    if browserlike and h.get("accept", "").strip() == "*/*":
        out.append(Signal("hdr.accept_any_only", BOT, w.weight("hdr.accept_any_only")))
    if (inp.http_version or "").upper() == "HTTP/1.0":
        out.append(Signal("http.version_1_0", BOT, w.weight("http.version_1_0")))
    if browserlike and _modern(agent) and inp.tls_version in ("TLS 1.0", "TLS 1.1"):
        out.append(
            Signal(
                "tls.outdated_for_browser",
                SPOOF,
                w.weight("tls.outdated_for_browser"),
                {"tls": inp.tls_version},
            )
        )

    chromium = agent.ua_family in CHROMIUM and agent.os_family != "iOS"
    major = _major(agent.ua_version)
    if chromium and not agent.is_inapp_webview and major is not None and major >= 90:
        brands_header = h.get("sec-ch-ua")
        if brands_header is None:
            out.append(
                Signal(
                    "hdr.chromium_without_client_hints",
                    SPOOF,
                    w.weight("hdr.chromium_without_client_hints"),
                )
            )
        else:
            brands = _brands(brands_header)
            engine = brands.get("Chromium") or brands.get("Google Chrome")
            if engine is None:
                out.append(
                    Signal(
                        "uach.brand_missing",
                        SPOOF,
                        w.weight("uach.brand_missing"),
                        {"brands": sorted(brands)},
                    )
                )
            elif agent.ua_family == "Chrome" and engine != major:
                out.append(
                    Signal(
                        "uach.version_mismatch",
                        SPOOF,
                        w.weight("uach.version_mismatch"),
                        {"ua_major": major, "hint_major": engine},
                    )
                )

    platform = _hint(h.get("sec-ch-ua-platform"))
    ua_os = parse(inp.user_agent).os_family
    if platform and ua_os:
        hinted = {"Chrome OS": "ChromeOS", "macOS": "macOS"}.get(platform, platform)
        # Chrome on Android's "desktop site" sends a Linux UA and an Android hint: real.
        desktop_site = hinted == "Android" and ua_os == "Linux"
        if hinted != ua_os and not desktop_site:
            out.append(
                Signal(
                    "uach.platform_mismatch",
                    SPOOF,
                    w.weight("uach.platform_mismatch"),
                    {"ua": ua_os, "hint": hinted},
                )
            )
    mobile_hint = (h.get("sec-ch-ua-mobile") or "").strip()
    if mobile_hint == "?1" and ua_os in DESKTOP_OS:
        out.append(Signal("uach.mobile_mismatch", SPOOF, w.weight("uach.mobile_mismatch")))
    return out


def _client_rules(inp: ClassifyInput, agent: ParsedAgent, w: ClassifierConfig) -> list[Signal]:
    if not inp.enriched:
        return []
    p = inp.probes
    out: list[Signal] = []
    desktop = agent.device_class is DeviceClass.DESKTOP
    native = not agent.is_inapp_webview
    chromium_desktop = agent.ua_family in CHROMIUM and desktop and native
    if p.get("webdriver") is True:
        out.append(Signal("client.webdriver", BOT, w.weight("client.webdriver")))
    if p.get("cdpArtefacts") is True:
        out.append(Signal("client.cdp_artefacts", BOT, w.weight("client.cdp_artefacts")))
    if p.get("permissionsAnomaly") is True:
        out.append(
            Signal("client.permissions_anomaly", BOT, w.weight("client.permissions_anomaly"))
        )
    if inp.gpu_renderer and SOFTWARE_RENDERERS.search(inp.gpu_renderer):
        out.append(
            Signal(
                "client.software_renderer",
                BOT,
                w.weight("client.software_renderer"),
                {"renderer": inp.gpu_renderer[:120]},
            )
        )
    if native and p.get("outerWidth") == 0:
        out.append(Signal("client.zero_outer_width", BOT, w.weight("client.zero_outer_width")))
    if chromium_desktop and p.get("chromeObject") is False:
        out.append(
            Signal("client.chrome_object_missing", BOT, w.weight("client.chrome_object_missing"))
        )
    if chromium_desktop and p.get("pluginCount") == 0:
        out.append(
            Signal(
                "client.no_plugins_desktop_chromium",
                BOT,
                w.weight("client.no_plugins_desktop_chromium"),
            )
        )
    fonts = p.get("fontCount")
    if desktop and isinstance(fonts, int) and fonts <= 1:
        out.append(Signal("client.few_fonts", BOT, w.weight("client.few_fonts"), {"fonts": fonts}))
    if inp.honeypot_tripped:
        out.append(Signal("client.honeypot", BOT, w.weight("client.honeypot")))
    return out


def _cross_checks(
    inp: ClassifyInput, agent: ParsedAgent, w: ClassifierConfig, ctx: Context
) -> list[Signal]:
    """ADR-0011's organising insight: the contradictions between claims."""
    out: list[Signal] = []
    os_family = parse(inp.user_agent).os_family
    renderer = inp.gpu_renderer or ""
    if renderer and os_family:
        lie = (
            (APPLE_GPU.search(renderer) and os_family in ("Windows", "Android", "Linux"))
            or (MOBILE_GPU.search(renderer) and os_family in ("Windows", "macOS"))
            or (DIRECT3D.search(renderer) and os_family in ("macOS", "iOS", "Android"))
        )
        if lie:
            out.append(
                Signal(
                    "xcheck.gpu_os",
                    SPOOF,
                    w.weight("xcheck.gpu_os"),
                    {"renderer": renderer[:120], "os": os_family},
                )
            )
    if os_family == "iOS" and inp.device_memory_gb is not None:
        # F5.AC5: Safari on iOS never exposes navigator.deviceMemory.
        out.append(
            Signal(
                "xcheck.ios_device_memory",
                SPOOF,
                w.weight("xcheck.ios_device_memory"),
                {"device_memory_gb": inp.device_memory_gb},
            )
        )
    phone = agent.device_class is DeviceClass.MOBILE
    if inp.enriched and phone and inp.touch_points == 0:
        out.append(Signal("xcheck.touch_mobile", SPOOF, w.weight("xcheck.touch_mobile")))
    if inp.screen_w and inp.screen_h:
        short, long_ = sorted((inp.screen_w, inp.screen_h))
        if (phone and short > 600) or (agent.device_class is DeviceClass.DESKTOP and long_ < 500):
            out.append(
                Signal(
                    "xcheck.screen_device_class",
                    SPOOF,
                    w.weight("xcheck.screen_device_class"),
                    {"screen": f"{inp.screen_w}x{inp.screen_h}", "class": agent.device_class.value},
                )
            )
    if phone and inp.cpu_cores is not None and inp.cpu_cores > 16:
        out.append(
            Signal(
                "xcheck.cores_device_class",
                SPOOF,
                w.weight("xcheck.cores_device_class"),
                {"cores": inp.cpu_cores},
            )
        )
    if inp.tz_countries and inp.country and inp.country.upper() not in inp.tz_countries:
        out.append(
            Signal(
                "xcheck.tz_country",
                SPOOF,
                w.weight("xcheck.tz_country"),
                {"country": inp.country, "tz_countries": sorted(inp.tz_countries)},
            )
        )
    if ctx.travel_kmh is not None and ctx.travel_kmh > w.impossible_travel_kmh:
        out.append(
            Signal(
                "xcheck.impossible_travel",
                SPOOF,
                w.weight("xcheck.impossible_travel"),
                {"kmh": round(ctx.travel_kmh)},
            )
        )
    return out


def _network_rules(inp: ClassifyInput, w: ClassifierConfig, ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    if inp.is_tor:
        out.append(Signal("net.tor_exit", NETWORK, 0))
    if inp.is_hosting:
        out.append(Signal("net.hosting_asn", NETWORK, 0, {"vpn_org": inp.is_vpn_org}))
    ptr = (inp.rdns_ptr or "").lower()
    if ptr and (hit := next((s for s in lists()["rdns_scanners"] if s in ptr), None)):
        out.append(Signal("rdns.scanner", SPAM, w.weight("rdns.scanner"), {"matched": hit}))
    if "capture.exploit_probe" in inp.capture_signals:
        out.append(Signal("capture.exploit_probe", SPAM, w.weight("capture.exploit_probe")))
    ceiling = w.rate_max_visits * (w.rate_mobile_multiplier if inp.is_mobile_network else 1)
    if ctx.prefix_visits > ceiling:
        out.append(
            Signal(
                "net.rate_anomaly",
                BOT,
                w.weight("net.rate_anomaly"),
                {"visits": ctx.prefix_visits, "window_min": w.rate_window_minutes},
            )
        )
    if ctx.fingerprint_asns >= w.collision_min_asns:
        out.append(
            Signal(
                "net.fingerprint_across_asns",
                SPOOF,
                w.weight("net.fingerprint_across_asns"),
                {"asns": ctx.fingerprint_asns},
            )
        )
    if ctx.prefix_fingerprints >= w.gateway_min_fingerprints:
        # F5.AC7's inverse: many devices behind one prefix is a gateway -- NAT or a
        # carrier -- and explicitly NOT a proxy. Recorded for the dashboard, weighted 0.
        out.append(
            Signal("net.shared_gateway", NETWORK, 0, {"fingerprints": ctx.prefix_fingerprints})
        )
    return out


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def classify(inp: ClassifyInput, ctx: Context, config: ClassifierConfig) -> Verdict:
    agent = parse(
        inp.user_agent,
        ch_platform=inp.headers.get("sec-ch-ua-platform"),
        ch_mobile=inp.headers.get("sec-ch-ua-mobile"),
    )
    signals = [
        *_ua_rules(inp, config),
        *_header_rules(inp, agent, config),
        *_client_rules(inp, agent, config),
        *_cross_checks(inp, agent, config, ctx),
        *_network_rules(inp, config, ctx),
    ]

    def total(category: str) -> int:
        return min(100, sum(s.weight for s in signals if s.category == category))

    bot, spoof, spam = total(BOT), total(SPOOF), total(SPAM)
    proxy = any(
        s.rule_id in ("net.fingerprint_across_asns", "xcheck.impossible_travel") for s in signals
    )
    datacenter = inp.is_hosting or inp.is_tor
    crawler = "ua.link_preview_fetcher" in inp.capture_signals

    # Precedence, ADR-0011 amendment item 7.
    if crawler:
        cls, reason = Classification.CRAWLER, "known_preview_fetcher"
    elif spam >= config.spam_threshold:
        cls, reason = Classification.SPAM, "malicious_automation"
    elif bot >= config.bot_threshold:
        cls, reason = Classification.BOT, "automation_evidence"
    elif datacenter:
        cls, reason = Classification.DATACENTER, "tor_exit" if inp.is_tor else "hosting_network"
    elif spoof >= config.spoof_threshold:
        cls, reason = Classification.SPOOFED, "contradicting_claims"
    elif (
        bot <= config.human_max_bot
        and spoof <= config.human_max_spoof
        and _plausible(inp, agent, signals)
    ):
        cls, reason = Classification.HUMAN, "enriched" if inp.enriched else "server_evidence"
    else:
        cls, reason = Classification.UNKNOWN, "inconclusive"

    return Verdict(
        classification=cls,
        bot_score=bot,
        spoof_score=spoof,
        signals=tuple(signals),
        is_datacenter=inp.is_hosting,
        is_vpn_suspected=inp.is_hosting and inp.is_vpn_org,
        is_tor=inp.is_tor,
        is_proxy_suspected=proxy,
        reason=reason,
    )


def _plausible(inp: ClassifyInput, agent: ParsedAgent, signals: list[Signal]) -> bool:
    """Positive evidence of a person, not merely the absence of a bot.

    Enriched: the page's script ran in something with a screen. Server-only (ADR-0011
    amendment item 6): a recognised browser whose header set raised nothing.
    """
    if agent.ua_family is None and not agent.is_inapp_webview:
        return False
    if inp.enriched:
        return inp.screen_w is not None
    return not any(s.rule_id.startswith(("hdr.", "uach.", "http.")) for s in signals)


def haversine_kmh(km: float, seconds: float) -> float:
    return math.inf if seconds <= 0 else km / (seconds / 3600)
