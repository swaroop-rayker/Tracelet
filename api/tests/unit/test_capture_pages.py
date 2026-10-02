"""The rendered capture page (F2.AC1-F2.AC5, F2.AC10, F2.AC12, RW-4).

The page must work with no JavaScript at all, load nothing from another origin, never
be cached, and never let a destination URL escape into script. The escaping tests pass
a destination directly to the renderer rather than through a link: a validated
``https://`` URL cannot normally carry ``</script>``, but the template must be safe on
its own terms, not only because validation happened to run first.
"""

from __future__ import annotations

import re

import pytest
from starlette.responses import Response

from tracelet.capture import pages

DEST = "https://example.com/landing?ref=bio"


def _page(**overrides: object) -> Response:
    kwargs: dict[str, object] = {
        "destination": DEST,
        "interstitial_ms": 700,
        "nonce": "N" * 48,
        "webview_host": None,
        "os_family": None,
    }
    kwargs.update(overrides)
    return pages.capture_page(**kwargs)  # type: ignore[arg-type]  # test helper forwards typed kwargs


def _body(response: Response) -> str:
    return bytes(response.body).decode("utf-8")


def _csp_nonce(response: Response) -> str:
    match = re.search(r"'nonce-([A-Za-z0-9_-]+)'", response.headers["content-security-policy"])
    assert match is not None
    return match.group(1)


# ---------------------------------------------------------------------------
# Works without JavaScript (F2.AC3)
# ---------------------------------------------------------------------------


def test_the_page_carries_a_noscript_refresh_to_the_destination() -> None:
    body = _body(_page())
    assert '<noscript><meta http-equiv="refresh" content="0;url=' in body
    assert "example.com/landing" in body


def test_the_continue_link_is_a_real_href() -> None:
    """Clickable with scripting off, and the one control a person can always use."""
    body = _body(_page())
    assert re.search(r'<a class="go" href="https://example\.com/landing\?ref=bio', body)
    assert "Continue now" in body


def test_the_notice_names_the_destination_and_links_the_privacy_page() -> None:
    """RW-4: one visible line, a privacy link, a Continue control."""
    body = _body(_page())
    assert "example.com" in body
    assert 'href="/privacy"' in body


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_the_page_is_never_cached() -> None:
    """A cached interstitial skips the server, and the visit is never recorded."""
    response = _page()
    assert "no-store" in response.headers["cache-control"]


def test_the_page_asks_not_to_be_indexed() -> None:
    assert "noindex" in _page().headers["x-robots-tag"]


def test_every_response_gets_a_fresh_csp_nonce() -> None:
    assert _csp_nonce(_page()) != _csp_nonce(_page())


def test_the_inline_style_and_script_carry_the_header_nonce() -> None:
    response = _page()
    nonce = _csp_nonce(response)
    body = _body(response)
    assert f'<style nonce="{nonce}">' in body
    assert f'<script nonce="{nonce}">' in body


def test_the_csp_permits_nothing_from_another_origin() -> None:
    """F2.AC12: no third-party request of any kind."""
    csp = _page().headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "://" not in csp, "no origin other than the page's own may appear"


def test_the_page_references_no_external_resource() -> None:
    body = _body(_page())
    for tag in ("<link ", "<img ", "<iframe", 'src="http'):
        assert tag not in body


def test_enrichment_is_a_json_post_not_a_pixel() -> None:
    """B6 fix 4: filter lists target pixel-shaped GETs and beacons; a same-origin JSON
    POST is neither."""
    body = _body(_page())
    assert 'fetch("/api/v1/s/"' in body
    assert 'method: "POST"' in body
    assert '"Content-Type": "application/json"' in body
    # Call shapes, not bare words: the script's comments explain why it avoids them.
    for shape in ("new Image(", ".sendBeacon(", "<img"):
        assert shape not in body


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_a_destination_cannot_close_the_script_tag() -> None:
    """``tojson`` escapes ``<``, so ``</script>`` in the destination stays data."""
    hostile = 'https://example.com/"</script><script>alert(1)</script>'
    body = _body(_page(destination=hostile))
    assert "<script>alert(1)</script>" not in body
    assert body.count("</script>") == 2, "only the two script blocks the template writes"


def test_a_destination_cannot_break_out_of_an_attribute() -> None:
    hostile = 'https://example.com/" onmouseover="alert(1)'
    body = _body(_page(destination=hostile))
    assert 'onmouseover="alert(1)"' not in body


# ---------------------------------------------------------------------------
# Enrichment and the fallback page
# ---------------------------------------------------------------------------


def test_the_enrichment_config_carries_the_nonce_and_the_timer() -> None:
    body = _body(_page(interstitial_ms=900))
    assert '"ms": 900' in body
    assert '"nonce": "' + "N" * 48 + '"' in body


def test_the_honeypot_is_present_and_hidden_from_people() -> None:
    body = _body(_page())
    assert 'class="hp" aria-hidden="true"' in body
    assert "/api/v1/hp/" + "N" * 48 in body
    assert 'tabindex="-1"' in body


def test_without_a_nonce_there_is_no_honeypot() -> None:
    """A honeypot link with no token would be a dead end that marks nothing."""
    assert "/api/v1/hp/" not in _body(_page(nonce=None))


def test_the_fallback_page_redirects_immediately_and_runs_no_script() -> None:
    """Used when the visit could not be recorded: nothing to enrich, so no timer --
    straight to the destination."""
    response = _page(enrich=False, nonce=None, status=503)
    body = _body(response)
    assert response.status_code == 503
    assert '<meta http-equiv="refresh" content="0;url=' in body
    assert "<script nonce" not in body


# ---------------------------------------------------------------------------
# Open-in-browser affordance (F2.AC10)
# ---------------------------------------------------------------------------


def test_an_android_webview_gets_an_intent_handoff() -> None:
    body = _body(_page(webview_host="instagram", os_family="Android"))
    assert "intent://example.com/landing" in body
    assert "package=com.android.chrome" in body
    # The fallback URL keeps the link working on a device with no Chrome.
    assert "S.browser_fallback_url=https%3A%2F%2Fexample.com" in body


def test_an_ios_webview_gets_a_copy_control_instead() -> None:
    """iOS has no reliable intent equivalent."""
    body = _body(_page(webview_host="instagram", os_family="iOS"))
    assert 'id="copy"' in body
    assert "intent://" not in body


@pytest.mark.parametrize(
    ("webview", "os_family"), [(None, "Android"), (None, "iOS"), ("instagram", "Windows")]
)
def test_no_affordance_outside_a_mobile_webview(webview: str | None, os_family: str) -> None:
    body = _body(_page(webview_host=webview, os_family=os_family))
    assert "intent://" not in body
    assert 'id="copy"' not in body


def test_the_intent_url_encodes_the_fallback() -> None:
    url = pages.android_intent("https://example.com/a?b=c&d=e")
    assert url.startswith("intent://example.com/a?b=c&d=e#Intent;scheme=https;")
    assert "S.browser_fallback_url=https%3A%2F%2Fexample.com%2Fa%3Fb%3Dc%26d%3De" in url


# ---------------------------------------------------------------------------
# Other pages
# ---------------------------------------------------------------------------


def test_the_not_found_page_says_nothing_about_which_links_exist() -> None:
    body = _body(pages.render("not_found.html", status=404))
    assert "not available" in body
    for revealing in ("inactive", "archived", "exists", "slug"):
        assert revealing not in body.lower()


def test_the_last_resort_still_redirects_when_the_destination_is_known() -> None:
    """F15.AC7: rendering failed, and the visitor is sent on anyway."""
    response = pages.last_resort(DEST)
    body = _body(response)
    assert response.status_code == 503
    assert 'http-equiv="refresh"' in body
    assert "example.com/landing" in body


def test_the_last_resort_without_a_destination_says_so() -> None:
    response = pages.last_resort(None)
    assert response.status_code == 503
    assert "unavailable" in _body(response).lower()


def test_the_rate_limited_redirect_is_a_302_that_is_never_cached() -> None:
    response = pages.redirect(DEST)
    assert response.status_code == 302
    assert response.headers["location"] == DEST
    assert "no-store" in response.headers["cache-control"]
