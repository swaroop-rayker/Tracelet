"""User-agent interpretation: browser, OS, device class, in-app webview, crawler.

**Deliberately modest.** This is not a complete UA parser and does not try to be.
It answers the questions M2 needs answered -- which app's webview is this (F2.AC9),
is this a link-preview fetcher (F2.AC8), roughly what device -- and returns ``None``
rather than guessing when it does not know (F3.AC5). ``ua-parser`` was considered and
rejected for now: its rule set compiles into every worker for a precision nothing in
M2 consumes. M4's classifier is the right place to revisit that, with evidence.

**Prefer Client Hints where they exist.** Chrome freezes the OS version and model in
the UA string, so ``Sec-CH-UA-Platform`` and ``Sec-CH-UA-Mobile`` are more truthful
than the string for the browsers that send them. A UA string is a claim either way;
cross-checking it against the hints and the client's own report is M4's job.

**The two mistakes that matter most** are both about Meta, and they point in opposite
directions:

* Facebook, Instagram and Messenger's **in-app browsers** carry ``FBAN``/``FBAV`` or
  ``Instagram`` in the UA. These are **humans** -- the largest visitor segment.
* ``facebookexternalhit`` and ``Facebot`` are Meta's **link-preview fetchers**. Instagram
  fetches a bio link the moment it is saved and again when it is shown; left
  unclassified, that prefetch would consume the human first-visit alert (ADR-0004,
  decision 9).

Get the first wrong and every Instagram visitor is a bot. Get the second wrong and every
link is "visited" before anyone has seen it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tracelet.capture.models import DeviceClass

# ---------------------------------------------------------------------------
# Link-preview fetchers (F2.AC8)
# ---------------------------------------------------------------------------

# Case-insensitive substrings; the FIRST match names the fetcher, so order matters.
# These fetch a URL to render a preview card. None of them is a person, none may be
# notified on, and all are excluded from analytics by default.
#
# Two rules for adding a needle, both learned writing this list:
#   * put the more specific fetcher first -- Telegram's is "TelegramBot (like
#     TwitterBot)", so twitterbot listed earlier would misname it;
#   * never match an app name alone -- "Pinterest/" also appears in Pinterest's own
#     in-app browser, which is a person. Match the fetcher's token, not the brand.
_CRAWLERS: Final[tuple[tuple[str, str], ...]] = (
    ("facebookexternalhit", "facebook"),
    ("facebot", "facebook"),
    ("meta-externalagent", "facebook"),
    ("telegrambot", "telegram"),
    ("linkedinbot", "linkedin"),
    ("twitterbot", "twitter"),
    ("redditbot", "reddit"),
    ("slackbot", "slack"),
    ("slack-imgproxy", "slack"),
    ("discordbot", "discord"),
    ("pinterestbot", "pinterest"),
    ("pinterest/0.", "pinterest"),
    ("skypeuripreview", "skype"),
    ("snap url preview", "snapchat"),
    ("vkshare", "vk"),
    ("iframely", "iframely"),
    ("embedly", "embedly"),
    ("googlebot", "google"),
    ("google-inspectiontool", "google"),
    ("bingbot", "bing"),
    ("applebot", "apple"),
    ("duckduckbot", "duckduckgo"),
    ("yandexbot", "yandex"),
    ("bytespider", "bytedance"),
    ("petalbot", "huawei"),
)

# WhatsApp's preview fetcher is "WhatsApp/2.23.20.0 A" -- no browser token at all. A
# person opening a link from WhatsApp uses their real browser, so a bare WhatsApp UA
# is always the fetcher.
_WHATSAPP_FETCHER = re.compile(r"^whatsapp/[\d.]+", re.IGNORECASE)


def crawler_name(user_agent: str | None) -> str | None:
    """The preview fetcher this UA belongs to, or ``None`` for anything else."""
    if not user_agent:
        return None
    lowered = user_agent.lower()
    for needle, name in _CRAWLERS:
        if needle in lowered:
            return name
    if _WHATSAPP_FETCHER.match(user_agent) and "mozilla" not in lowered:
        return "whatsapp"
    return None


# ---------------------------------------------------------------------------
# In-app webviews (F2.AC9)
# ---------------------------------------------------------------------------

# Checked in order: Messenger before Facebook, because Messenger's UA also carries
# the generic FBAN/FBAV tokens.
_WEBVIEWS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\bInstagram\b"), "instagram"),
    (re.compile(r"FBAN/Messenger|FB_IAB/MESSENGER|\bMessenger\b", re.IGNORECASE), "messenger"),
    (re.compile(r"FBAN/|FBAV/|FB_IAB/|\[FB", re.IGNORECASE), "facebook"),
    (re.compile(r"LinkedInApp", re.IGNORECASE), "linkedin"),
    (re.compile(r"\bReddit/|reddit(?:\s|/)", re.IGNORECASE), "reddit"),
    (re.compile(r"Snapchat", re.IGNORECASE), "snapchat"),
    (re.compile(r"TwitterAndroid|Twitter for iP", re.IGNORECASE), "x"),
    (re.compile(r"musical_ly|BytedanceWebview|\bTikTok\b|trill_", re.IGNORECASE), "tiktok"),
    (re.compile(r"\bLine/\d", re.IGNORECASE), "line"),
    (re.compile(r"MicroMessenger", re.IGNORECASE), "wechat"),
)


def webview_host(user_agent: str | None) -> str | None:
    """Which app's in-app browser this is, or ``None`` for a normal browser."""
    if not user_agent:
        return None
    for pattern, host in _WEBVIEWS:
        if pattern.search(user_agent):
            return host
    return None


# ---------------------------------------------------------------------------
# Browser, OS, device
# ---------------------------------------------------------------------------

# Order matters: every Chromium derivative also says "Chrome", and Chrome says
# "Safari". Most specific first.
_BROWSERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"EdgiOS/([\d.]+)"), "Edge"),
    (re.compile(r"EdgA/([\d.]+)"), "Edge"),
    (re.compile(r"Edg/([\d.]+)"), "Edge"),
    (re.compile(r"OPR/([\d.]+)"), "Opera"),
    (re.compile(r"SamsungBrowser/([\d.]+)"), "Samsung Internet"),
    (re.compile(r"UCBrowser/([\d.]+)"), "UC Browser"),
    (re.compile(r"YaBrowser/([\d.]+)"), "Yandex"),
    (re.compile(r"Brave/([\d.]+)"), "Brave"),
    (re.compile(r"CriOS/([\d.]+)"), "Chrome"),
    (re.compile(r"FxiOS/([\d.]+)"), "Firefox"),
    (re.compile(r"Firefox/([\d.]+)"), "Firefox"),
    (re.compile(r"Chrome/([\d.]+)"), "Chrome"),
    (re.compile(r"Version/([\d.]+).*Safari/"), "Safari"),
)

_WINDOWS_NT: Final[dict[str, str]] = {
    # Windows 11 still reports NT 10.0 in the UA string; only Client Hints can tell
    # them apart, so this deliberately says "10" rather than guessing "11".
    "10.0": "10",
    "6.3": "8.1",
    "6.2": "8",
    "6.1": "7",
}

_TV = re.compile(
    r"SmartTV|SMART-TV|\bTizen\b.*TV|WebOS|NetCast|HbbTV|AppleTV|GoogleTV|BRAVIA|AFT[A-Z]",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedAgent:
    ua_family: str | None
    ua_version: str | None
    os_family: str | None
    os_version: str | None
    device_class: DeviceClass
    webview_host: str | None
    crawler: str | None

    @property
    def is_inapp_webview(self) -> bool:
        return self.webview_host is not None


def _major_minor(version: str) -> str:
    """``131.0.6778.85`` -> ``131.0``. Patch levels add noise, not information."""
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) > 1 else parts[0]


def _browser(ua: str) -> tuple[str | None, str | None]:
    for pattern, family in _BROWSERS:
        match = pattern.search(ua)
        if match:
            return family, _major_minor(match.group(1))
    return None, None


def _os(ua: str) -> tuple[str | None, str | None]:
    if match := re.search(r"Windows NT ([\d.]+)", ua):
        return "Windows", _WINDOWS_NT.get(match.group(1), match.group(1))
    # iPadOS 13+ in desktop mode claims to be a Mac; the client's touch-point count
    # is what gives it away, and that arrives with enrichment (M4 cross-check).
    if match := re.search(r"(?:iPhone|iPad|iPod).*?OS (\d+[_\d]*)", ua):
        return "iOS", match.group(1).replace("_", ".")
    if match := re.search(r"Android (\d+(?:\.\d+)?)", ua):
        return "Android", match.group(1)
    if "CrOS" in ua:
        return "ChromeOS", None
    if match := re.search(r"Mac OS X (\d+[_.\d]*)", ua):
        return "macOS", match.group(1).replace("_", ".")
    if "Linux" in ua:
        return "Linux", None
    return None, None


def _client_hint_platform(value: str | None) -> str | None:
    """``"Windows"`` -> ``Windows``. Client Hints are structured-header quoted strings."""
    if not value:
        return None
    cleaned = value.strip().strip('"')
    return {"macOS": "macOS", "Chrome OS": "ChromeOS"}.get(cleaned, cleaned) or None


def _device(ua: str, os_family: str | None, ch_mobile: str | None) -> DeviceClass:
    if _TV.search(ua):
        return DeviceClass.TV
    if "iPad" in ua or "Tablet" in ua or ("Android" in ua and "Mobile" not in ua):
        return DeviceClass.TABLET
    if ch_mobile is not None:
        # Structured-header boolean: ?1 or ?0. More truthful than the string.
        return DeviceClass.MOBILE if ch_mobile.strip() == "?1" else DeviceClass.DESKTOP
    if "Mobile" in ua or "iPhone" in ua or "iPod" in ua:
        return DeviceClass.MOBILE
    if os_family in {"Windows", "macOS", "Linux", "ChromeOS"}:
        return DeviceClass.DESKTOP
    return DeviceClass.UNKNOWN


def parse(
    user_agent: str | None,
    *,
    ch_platform: str | None = None,
    ch_mobile: str | None = None,
) -> ParsedAgent:
    """Interpret a user agent, preferring Client Hints where they are present."""
    ua = user_agent or ""
    crawler = crawler_name(ua)
    family, version = _browser(ua)
    os_family, os_version = _os(ua)

    hinted = _client_hint_platform(ch_platform)
    if hinted and hinted != os_family:
        # The hint wins, but the version from a string describing a different OS is
        # meaningless, so it is dropped rather than paired with the wrong platform.
        os_family, os_version = hinted, None

    device = DeviceClass.BOT if crawler else _device(ua, os_family, ch_mobile)
    if not ua:
        device = DeviceClass.UNKNOWN

    return ParsedAgent(
        ua_family=family,
        ua_version=version,
        os_family=os_family,
        os_version=os_version,
        device_class=device,
        webview_host=None if crawler else webview_host(ua),
        crawler=crawler,
    )
