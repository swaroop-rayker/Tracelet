"""User-agent interpretation: webviews (F2.AC9), preview fetchers (F2.AC8), device.

The strings are real user agents, lightly trimmed. The Meta cases are the ones that
matter most and they point in opposite directions: an in-app browser is a *person*,
a preview fetcher is *not*. Confusing them either turns every Instagram visitor into a
bot, or lets Instagram's own prefetch consume the human first-visit alert.
"""

from __future__ import annotations

import pytest

from tracelet.capture.models import DeviceClass
from tracelet.capture.useragent import crawler_name, parse, webview_host

IG_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 Instagram 337.0.3.23.54 (iPhone15,2; iOS 17_5; "
    "en_IN; en; scale=3.00; 1179x2556; 612271240)"
)
IG_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/131.0.6778.39 Mobile "
    "Safari/537.36 Instagram 356.0.0.41.101 Android (34/14; 450dpi; 1080x2340; samsung)"
)
FB_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/470.0.0.40.94;FBDV/iPhone15,2;"
    "FBMD/iPhone;FBSN/iOS;FBSV/17.5;FBSS/3;FBID/phone;FBLC/en_US;FBOP/5]"
)
MESSENGER_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 [FBAN/MessengerForiOS;FBAV/470.0.0.28.109]"
)
WECHAT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003133) NetType/WIFI"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)
SAFARI_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/131.0.2903.86"
SAMSUNG = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36"
)
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0"
IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
SMART_TV = (
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 7.0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/5.0 Chrome/85.0.4183.93 TV Safari/537.36"
)


# ---------------------------------------------------------------------------
# The Meta distinction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ua", [IG_IOS, IG_ANDROID, FB_IOS, MESSENGER_IOS])
def test_a_meta_in_app_browser_is_a_person_not_a_fetcher(ua: str) -> None:
    """The largest visitor segment. Classifying these as crawlers would erase it."""
    parsed = parse(ua)
    assert parsed.crawler is None
    assert parsed.device_class is not DeviceClass.BOT
    assert parsed.is_inapp_webview


@pytest.mark.parametrize(
    "ua",
    [
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "facebookexternalhit/1.1",
        "Facebot",
        "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
    ],
)
def test_metas_preview_fetcher_is_a_crawler(ua: str) -> None:
    """Instagram fetches a bio link when it is saved and again when it is shown. Left
    unclassified, that would consume the human first-visit alert (ADR-0004 decision 9)."""
    parsed = parse(ua)
    assert parsed.crawler == "facebook"
    assert parsed.device_class is DeviceClass.BOT
    assert parsed.webview_host is None, "a fetcher is never also a webview"


# ---------------------------------------------------------------------------
# Preview fetchers (F2.AC8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ua", "name"),
    [
        (
            "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
            "linkedin",
        ),
        ("Twitterbot/1.0", "twitter"),
        ("Mozilla/5.0 (compatible; redditbot/1.0; +http://www.reddit.com/feedback)", "reddit"),
        ("WhatsApp/2.23.20.0 A", "whatsapp"),
        ("TelegramBot (like TwitterBot)", "telegram"),
        ("Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "slack"),
        ("Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)", "discord"),
        ("Pinterest/0.2 (+http://www.pinterest.com/bot.html)", "pinterest"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "google"),
        (
            # iMessage's preview fetcher claims to be three bots at once.
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.4 (KHTML, "
            "like Gecko) Version/9.0.1 Safari/601.2.4 facebookexternalhit/1.1 Facebot "
            "Twitterbot/1.0",
            "facebook",
        ),
    ],
)
def test_preview_fetchers_are_named(ua: str, name: str) -> None:
    assert crawler_name(ua) == name


def test_telegrams_fetcher_is_not_misnamed_as_twitter() -> None:
    """Its UA is "TelegramBot (like TwitterBot)". Order in the needle list decides."""
    assert crawler_name("TelegramBot (like TwitterBot)") == "telegram"


@pytest.mark.parametrize(
    "ua",
    [
        CHROME_ANDROID,
        SAFARI_IOS,
        IG_IOS,
        FB_IOS,
        # Pinterest's own in-app browser, which is a person. An earlier needle,
        # "pinterest/", matched this.
        SAFARI_IOS + " [Pinterest/iOS]",
        # LinkedIn's app, not LinkedInBot.
        SAFARI_IOS + " [LinkedInApp]/9.29.8984",
        # A real browser opened from WhatsApp carries no WhatsApp token at all; one
        # that does and also looks like a browser is not the bare fetcher.
        CHROME_ANDROID + " WhatsApp/2.24.1",
    ],
)
def test_people_are_not_crawlers(ua: str) -> None:
    assert crawler_name(ua) is None


# ---------------------------------------------------------------------------
# Webviews (F2.AC9) -- all ten the requirement names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ua", "host"),
    [
        (IG_IOS, "instagram"),
        (IG_ANDROID, "instagram"),
        (FB_IOS, "facebook"),
        (MESSENGER_IOS, "messenger"),
        (SAFARI_IOS + " [LinkedInApp]/9.29.8984", "linkedin"),
        (CHROME_ANDROID + " Reddit/Version 2024.20.0/Build 1531023/Android 14", "reddit"),
        (SAFARI_IOS + " Snapchat/12.95.0.40 (like Safari/8617.1.17.10.9, panda)", "snapchat"),
        (CHROME_ANDROID + " TwitterAndroid", "x"),
        (SAFARI_IOS + " Twitter for iPhone/10.50", "x"),
        (SAFARI_IOS + " musical_ly_34.1.0 JsSdk/2.0 NetType/WIFI", "tiktok"),
        (CHROME_ANDROID + " BytedanceWebview/d8a21c6", "tiktok"),
        (SAFARI_IOS + " Line/13.20.0", "line"),
        (WECHAT, "wechat"),
    ],
)
def test_every_required_webview_is_recognised(ua: str, host: str) -> None:
    assert webview_host(ua) == host


def test_wechat_is_not_mistaken_for_messenger() -> None:
    """ "MicroMessenger" contains "Messenger". The word boundary is what separates them."""
    assert webview_host(WECHAT) == "wechat"


@pytest.mark.parametrize("ua", [CHROME_ANDROID, SAFARI_IOS, CHROME_WINDOWS, FIREFOX_MAC, "", None])
def test_ordinary_browsers_are_not_webviews(ua: str | None) -> None:
    assert webview_host(ua) is None


# ---------------------------------------------------------------------------
# Browser, OS, device
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ua", "family", "version"),
    [
        (CHROME_ANDROID, "Chrome", "131.0"),
        (SAFARI_IOS, "Safari", "17.5"),
        (EDGE_WINDOWS, "Edge", "131.0"),
        (SAMSUNG, "Samsung Internet", "26.0"),
        (FIREFOX_MAC, "Firefox", "132.0"),
        (IG_ANDROID, "Chrome", "131.0"),
    ],
)
def test_browser_family_and_version(ua: str, family: str, version: str) -> None:
    parsed = parse(ua)
    assert (parsed.ua_family, parsed.ua_version) == (family, version)


def test_chromes_reduced_version_is_read_not_mangled() -> None:
    """``Chrome/131.0.0.0`` is also a valid IPv4 address; see docs/ERRORS.md E23."""
    assert parse(CHROME_ANDROID).ua_version == "131.0"


def test_an_ios_webview_reports_no_browser_rather_than_a_guess() -> None:
    """There is no Safari token in an iOS in-app browser's UA. None, not "Safari"."""
    assert parse(IG_IOS).ua_family is None


@pytest.mark.parametrize(
    ("ua", "os_family", "os_version"),
    [
        (CHROME_ANDROID, "Android", "14"),
        (SAFARI_IOS, "iOS", "17.5"),
        (IPAD, "iOS", "17.5"),
        (CHROME_WINDOWS, "Windows", "10"),
        (FIREFOX_MAC, "macOS", "14.5"),
    ],
)
def test_os_family_and_version(ua: str, os_family: str, os_version: str) -> None:
    parsed = parse(ua)
    assert (parsed.os_family, parsed.os_version) == (os_family, os_version)


def test_windows_11_is_not_claimed_from_the_ua_string() -> None:
    """Windows 11 still reports NT 10.0. Only Client Hints can tell them apart, so the
    string alone says 10 rather than guessing."""
    assert parse(CHROME_WINDOWS).os_version == "10"


@pytest.mark.parametrize(
    ("ua", "device"),
    [
        (CHROME_ANDROID, DeviceClass.MOBILE),
        (SAFARI_IOS, DeviceClass.MOBILE),
        (IPAD, DeviceClass.TABLET),
        (ANDROID_TABLET, DeviceClass.TABLET),
        (CHROME_WINDOWS, DeviceClass.DESKTOP),
        (FIREFOX_MAC, DeviceClass.DESKTOP),
        (SMART_TV, DeviceClass.TV),
        ("", DeviceClass.UNKNOWN),
    ],
)
def test_device_class(ua: str, device: DeviceClass) -> None:
    assert parse(ua).device_class is device


# ---------------------------------------------------------------------------
# Client Hints
# ---------------------------------------------------------------------------


def test_the_platform_hint_overrides_the_string_and_drops_its_version() -> None:
    """A hint naming a different OS wins, and the version parsed from a string about
    another platform is discarded rather than paired with the wrong one."""
    parsed = parse(CHROME_ANDROID, ch_platform='"Windows"')
    assert parsed.os_family == "Windows"
    assert parsed.os_version is None


def test_an_agreeing_hint_keeps_the_version() -> None:
    parsed = parse(CHROME_ANDROID, ch_platform='"Android"')
    assert (parsed.os_family, parsed.os_version) == ("Android", "14")


@pytest.mark.parametrize(
    ("hint", "device"), [("?1", DeviceClass.MOBILE), ("?0", DeviceClass.DESKTOP)]
)
def test_the_mobile_hint_decides_mobile_versus_desktop(hint: str, device: DeviceClass) -> None:
    assert parse(CHROME_WINDOWS, ch_mobile=hint).device_class is device


def test_no_user_agent_at_all() -> None:
    parsed = parse(None)
    assert parsed.ua_family is None
    assert parsed.os_family is None
    assert parsed.device_class is DeviceClass.UNKNOWN
    assert parsed.crawler is None
    assert parsed.is_inapp_webview is False
