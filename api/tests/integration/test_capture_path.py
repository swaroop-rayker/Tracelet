"""The capture path end to end, against the real database (F2, F3, F11.AC3, F13.AC6).

The governing rule is CLAUDE.md invariant 1: the redirect must never fail. Several
tests here break something on purpose -- the database, the renderer -- and assert the
visitor is still sent on.

The privacy tests search the whole stored row, and the API response, for a distinctive
visitor address. The claim being tested is "appears nowhere", so the test looks
everywhere rather than at the columns someone thought to check.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.conftest import BASE_URL
from tests.integration import capture_helpers as ch
from tests.integration.helpers import AUTH, ClientFactory, SignedIn
from tracelet.capture import pages, service
from tracelet.capture.models import (
    Classification,
    DeviceClass,
    VisitStage,
)
from tracelet.config import Settings
from tracelet.crypto.envelope import Envelope, open_str
from tracelet.db.engine import get_engine, session_scope
from tracelet.main import create_app
from tracelet.middleware import TRACE_HEADER

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The visit exists before the response (F2.AC1, F2.AC2)
# ---------------------------------------------------------------------------


async def test_a_visit_is_committed_before_the_page_is_returned(db_client: AsyncClient) -> None:
    link = await ch.create_link()

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    stored = await ch.latest_visit(link.id)
    assert stored.stage is VisitStage.SERVER
    assert stored.finalized_at is None
    assert stored.trace_id == response.headers[TRACE_HEADER], "one id ties row to request"


async def test_the_capture_url_never_answers_with_a_redirect(db_client: AsyncClient) -> None:
    """F2.AC1. A 302 offers no collection opportunity and is the open-redirector
    signature Safe Browsing flags (B4)."""
    link = await ch.create_link()
    response = await ch.visit(db_client, link.slug)
    assert response.status_code == 200
    assert "location" not in response.headers


async def test_a_visit_with_javascript_disabled_is_still_recorded_and_finalised(
    db_client: AsyncClient,
) -> None:
    """The M2 done-check, at the server: no enrichment ever arrives, and the visit is
    finalised by the sweeper as ``server_only``. Nothing is lost to a silent client."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    pending = await ch.latest_visit(link.id)

    await ch.backdate(pending.id, seconds=91)
    async with session_scope() as db:
        await service.sweep(db)

    finalised = await ch.get_visit(pending.id)
    assert finalised.stage is VisitStage.SERVER_ONLY
    assert finalised.finalized_at is not None


async def test_the_page_is_never_cached(db_client: AsyncClient) -> None:
    """A cached interstitial skips the server, and the visit is never recorded."""
    link = await ch.create_link()
    response = await ch.visit(db_client, link.slug)
    assert "no-store" in response.headers["cache-control"]


# ---------------------------------------------------------------------------
# 404s leak nothing (F2.AC14)
# ---------------------------------------------------------------------------


def _without_nonce(html: str) -> str:
    return re.sub(r'nonce="[^"]+"', 'nonce=""', html)


async def test_unknown_inactive_and_archived_slugs_are_indistinguishable(
    db_client: AsyncClient,
) -> None:
    inactive = await ch.create_link(is_active=False)
    archived = await ch.create_link(archived=True)

    unknown_response = await ch.visit(db_client, ch.new_slug())
    inactive_response = await ch.visit(db_client, inactive.slug)
    archived_response = await ch.visit(db_client, archived.slug)

    responses = (unknown_response, inactive_response, archived_response)
    assert {r.status_code for r in responses} == {404}
    assert len({_without_nonce(r.text) for r in responses}) == 1, "the bodies must not differ"
    assert await ch.visit_count(inactive.id) == 0, "an inactive link records nothing"


@pytest.mark.parametrize("slug", ["ab", "UPPER-CASE-LINK", "has space", "a" * 40, "..%2f.."])
async def test_a_slug_that_cannot_exist_is_a_404(db_client: AsyncClient, slug: str) -> None:
    response = await db_client.get(f"/r/{slug}", headers={"User-Agent": ch.CHROME_UA})
    assert response.status_code == 404


async def test_a_slug_matches_case_insensitively(db_client: AsyncClient) -> None:
    """Typed from a screenshot or a poster, a slug arrives in whatever case."""
    link = await ch.create_link()
    response = await ch.visit(db_client, link.slug.upper())
    assert response.status_code == 200
    assert await ch.visit_count(link.id) == 1


# ---------------------------------------------------------------------------
# The destination comes only from the row (F1.AC7, F13.AC3)
# ---------------------------------------------------------------------------


async def test_nothing_in_the_request_can_choose_the_destination(db_client: AsyncClient) -> None:
    """The structural remedy for B4. Every obvious open-redirect parameter is tried."""
    link = await ch.create_link()
    evil = "https://evil.example/phish"

    response = await ch.visit(
        db_client,
        link.slug,
        query=f"?url={evil}&dest={evil}&redirect={evil}&next={evil}&to={evil}",
        headers={"X-Destination": evil, "Referer": evil, "X-Original-URL": evil},
    )

    assert response.status_code == 200
    assert "evil.example" not in response.text, "nothing from the request is echoed"
    assert f'"dest": "{ch.DESTINATION}"' in response.text


# ---------------------------------------------------------------------------
# What is recorded (F3.AC1, F2.AC8, F2.AC9)
# ---------------------------------------------------------------------------


async def test_server_signals_are_recorded(db_client: AsyncClient) -> None:
    link = await ch.create_link()

    await ch.visit(
        db_client,
        link.slug,
        ua=ch.IG_ANDROID_UA,
        query="?utm_source=ig&utm_campaign=bio",
        headers={"Accept-Language": "en-IN,en;q=0.9", "Referer": "https://l.instagram.com/"},
    )

    stored = await ch.latest_visit(link.id)
    assert stored.is_inapp_webview is True
    assert stored.webview_host == "instagram"
    assert stored.device_class is DeviceClass.MOBILE
    assert (stored.os_family, stored.os_version) == ("Android", "14")
    assert stored.utm == {"utm_source": "ig", "utm_campaign": "bio"}
    assert stored.referer == "https://l.instagram.com/"
    assert stored.request_headers is not None
    assert stored.request_headers["accept-language"] == "en-IN,en;q=0.9"
    assert stored.classification is Classification.UNKNOWN
    assert stored.classifier_version == service.CLASSIFIER_VERSION


async def test_a_preview_fetcher_is_recorded_as_a_crawler(db_client: AsyncClient) -> None:
    """The M2 done-check: an Instagram prefetch appears as ``crawler``. The page it is
    served is identical -- serving crawlers something different would be cloaking."""
    link = await ch.create_link()
    human = await ch.visit(db_client, link.slug)
    fetcher = await ch.visit(db_client, link.slug, ua=ch.FB_FETCHER_UA)

    stored = await ch.latest_visit(link.id)
    assert stored.classification is Classification.CRAWLER
    assert stored.device_class is DeviceClass.BOT
    assert any(s["rule_id"] == "ua.link_preview_fetcher" for s in stored.signals)
    assert _without_nonce(human.text).count("Continue now") == _without_nonce(fetcher.text).count(
        "Continue now"
    )


# ---------------------------------------------------------------------------
# The address (F12.AC1, RW-3, CLAUDE.md invariant 4)
# ---------------------------------------------------------------------------


async def test_the_address_is_stored_only_as_hmac_prefix_and_ciphertext(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    await ch.visit(db_client, link.slug, peer=ch.VISITOR_IP)
    stored = await ch.latest_visit(link.id)

    assert str(stored.ip_prefix) == ch.VISITOR_PREFIX
    assert stored.ip_hmac is not None
    assert len(stored.ip_hmac) == 32
    assert stored.ip_enc is not None
    assert stored.ip_purge_after is not None

    # The ciphertext opens only with this visit's id as AAD (ADR-0007).
    assert stored.ip_key_version is not None
    sealed = Envelope(key_version=stored.ip_key_version, payload=stored.ip_enc)
    assert open_str(sealed, aad=str(stored.id), key_path=str(integration_settings.ip_key_file)) == (
        ch.VISITOR_IP
    )


async def test_the_address_appears_nowhere_in_the_stored_row(db_client: AsyncClient) -> None:
    """The M2 done-check: no plaintext IP anywhere. The address is sent in every way a
    request can carry it, and the whole row is searched."""
    link = await ch.create_link()

    await ch.visit(
        db_client,
        link.slug,
        peer=ch.VISITOR_IP,
        headers={
            "X-Forwarded-For": f"{ch.VISITOR_IP}, 10.0.0.1",
            "X-Real-IP": ch.VISITOR_IP,
            "Forwarded": f"for={ch.VISITOR_IP}",
            "CF-Connecting-IP": ch.VISITOR_IP,
            "X-Some-Proxy": f"client={ch.VISITOR_IP}",
            "Referer": f"https://{ch.VISITOR_IP}/page",
        },
        query=f"?utm_source={ch.VISITOR_IP}",
    )

    stored = await ch.latest_visit(link.id)
    assert ch.VISITOR_IP not in await ch.row_as_text(stored.id)


async def test_the_schema_has_no_column_that_could_hold_an_address(db_app: object) -> None:
    """Not "usually empty" -- the column does not exist (ADR-0007). The only INET column
    is the prefix, and every value in it is a network, never a host."""
    del db_app
    async with get_engine().connect() as conn:
        inet_columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'visits' AND data_type IN ('inet', 'cidr')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(inet_columns) == ["ip_prefix"]


async def test_every_stored_prefix_is_a_network(db_client: AsyncClient) -> None:
    link = await ch.create_link()
    await ch.visit(db_client, link.slug, peer="2401:4900:1c2e:5a7b::1")
    stored = await ch.latest_visit(link.id)
    network = ipaddress.ip_interface(str(stored.ip_prefix)).network
    assert network.prefixlen == 48


async def test_an_admins_session_cookie_is_never_stored(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The owner's browser sends its session cookie to /r/ too. The sanitiser drops
    every credential header, so a visit can never carry a live session."""
    del owner
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    stored = await ch.latest_visit(link.id)
    assert stored.request_headers is not None
    assert "cookie" not in stored.request_headers
    assert "tracelet_session" not in await ch.row_as_text(stored.id)


# ---------------------------------------------------------------------------
# Cloudflare (F13.AC6)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        yield client


async def test_a_forged_cf_header_is_ignored_in_direct_mode(db_client: AsyncClient) -> None:
    """The M2 done-check. Not behind Cloudflare, so a CF header is always forged."""
    link = await ch.create_link()

    await ch.visit(
        db_client,
        link.slug,
        peer=ch.VISITOR_IP,
        headers={"CF-Connecting-IP": "8.8.8.8", "CF-IPCountry": "US", "CF-Ray": "abc-SFO"},
    )

    stored = await ch.latest_visit(link.id)
    assert str(stored.ip_prefix) == ch.VISITOR_PREFIX, "the real peer, not the claim"
    assert stored.cf_country is None
    assert stored.cf_colo is None
    assert any(s["rule_id"] == "edge.unverified_cf_header" for s in stored.signals)


async def test_a_forged_cf_header_from_a_non_cloudflare_peer_is_ignored_behind_cloudflare(
    integration_settings: Settings,
) -> None:
    """Behind Cloudflare, but this request came straight to the origin."""
    link = await ch.create_link()
    settings = integration_settings.model_copy(update={"behind_cloudflare": True})

    async with _client_for(settings) as client:
        await ch.visit(
            client,
            link.slug,
            peer=ch.VISITOR_IP,
            headers={"CF-Connecting-IP": "8.8.8.8", "CF-IPCountry": "US"},
        )

    stored = await ch.latest_visit(link.id)
    assert str(stored.ip_prefix) == ch.VISITOR_PREFIX
    assert stored.cf_country is None


async def test_a_verified_cloudflare_edge_is_believed(integration_settings: Settings) -> None:
    """The positive case, so the guard above is not simply refusing everything."""
    link = await ch.create_link()
    settings = integration_settings.model_copy(update={"behind_cloudflare": True})

    async with _client_for(settings) as client:
        await ch.visit(
            client,
            link.slug,
            peer="162.158.1.10",  # inside 162.158.0.0/15
            headers={
                "CF-Connecting-IP": ch.VISITOR_IP,
                "CF-IPCountry": "IN",
                "CF-Ray": "8b1c2d3e4f5a6b7c-BLR",
            },
        )

    stored = await ch.latest_visit(link.id)
    assert str(stored.ip_prefix) == ch.VISITOR_PREFIX
    assert (stored.cf_country, stored.cf_colo) == ("IN", "BLR")
    assert not any(s["rule_id"] == "edge.unverified_cf_header" for s in stored.signals)


# ---------------------------------------------------------------------------
# Rate limiting still redirects (F11.AC3)
# ---------------------------------------------------------------------------


async def test_a_rate_limited_visitor_is_still_redirected(db_client: AsyncClient) -> None:
    """The M2 done-check. Abuse control must never punish a human: over the limit, the
    visitor goes straight to the destination and the shedding is recorded."""
    link = await ch.create_link()

    statuses = [(await ch.visit(db_client, link.slug)).status_code for _ in range(10)]
    over = await ch.visit(db_client, link.slug)

    assert statuses == [200] * 10, "the burst allowance is ten"
    assert over.status_code == 302
    assert over.headers["location"] == ch.DESTINATION

    stored = await ch.latest_visit(link.id)
    assert stored.stage is VisitStage.RATE_LIMITED
    assert stored.finalized_at is not None, "not waiting for a sweeper that should skip it"


async def test_a_rate_limited_row_carries_nothing_the_client_supplied(
    db_client: AsyncClient,
) -> None:
    """DATA_MODEL section 5.3, invariant 8: it exists to make shedding visible, and a
    request being shed gets no capture."""
    link = await ch.create_link()
    for _ in range(10):
        await ch.visit(db_client, link.slug)
    await ch.visit(db_client, link.slug, headers={"Referer": "https://x.example/"})

    stored = await ch.latest_visit(link.id)
    assert stored.stage is VisitStage.RATE_LIMITED
    assert stored.user_agent is None
    assert stored.request_headers is None
    assert stored.referer is None
    assert stored.ip_enc is None
    assert stored.ip_hmac is None
    assert stored.ip_prefix is not None, "the network is kept: it is what was limited"


async def test_the_limit_is_per_network(db_client: AsyncClient) -> None:
    link = await ch.create_link()
    for _ in range(11):
        await ch.visit(db_client, link.slug, peer=ch.VISITOR_IP)

    other = await ch.visit(db_client, link.slug, peer="203.0.113.50")
    assert other.status_code == 200


# ---------------------------------------------------------------------------
# The redirect never fails (CLAUDE.md invariant 1, F15.AC6, F15.AC7)
# ---------------------------------------------------------------------------


class _UnreachableDatabase:
    """Stands in for ``session_scope`` with a database that refuses every connection."""

    async def __aenter__(self) -> object:
        msg = "database is unreachable"
        raise ConnectionRefusedError(msg)

    async def __aexit__(self, *exc: object) -> None:
        return None


def _broken_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "session_scope", _UnreachableDatabase)


async def test_a_database_outage_still_redirects_a_known_link(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link is in the cache from an earlier request, so the visitor is sent on even
    though nothing can be recorded."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)  # primes the cache
    _broken_database(monkeypatch)

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 503
    assert f'content="0;url={ch.DESTINATION}"' in response.text
    assert "Continue now" in response.text


async def test_a_database_outage_with_an_unknown_link_says_so(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one outcome that cannot redirect: there is nowhere to redirect to."""
    link = await ch.create_link()
    _broken_database(monkeypatch)

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.text.lower()
    assert ch.DESTINATION not in response.text


async def test_a_rendering_failure_still_redirects(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F15.AC7: an exception after capture decided what to do still ends at the
    destination, through a fixed string that cannot itself throw."""
    link = await ch.create_link()

    def explode(**_: object) -> object:
        msg = "template error"
        raise RuntimeError(msg)

    monkeypatch.setattr(pages, "capture_page", explode)
    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 503
    assert 'http-equiv="refresh"' in response.text
    assert "example.com/landing" in response.text
    assert await ch.visit_count(link.id) == 1, "the visit was recorded before rendering failed"


async def test_no_exception_ever_reaches_a_visitor_as_json(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever breaks, the capture path answers in HTML."""
    link = await ch.create_link()
    _broken_database(monkeypatch)
    response = await ch.visit(db_client, link.slug)
    assert "application/problem+json" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# The privacy notice (F2.AC13)
# ---------------------------------------------------------------------------


async def test_the_privacy_notice_is_public_and_complete(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    response = await db_client.get("/privacy")

    assert response.status_code == 200
    body = response.text
    assert f"{integration_settings.retention_ip_days} days" in body
    assert f"{integration_settings.retention_visit_days} days" in body
    for attribution in ("DB-IP", "GeoNames", "GeoLite2", "IP2Location", "IPinfo"):
        assert attribution in body, f"missing the required {attribution} attribution"
    assert "CC BY 4.0" in body


async def test_the_privacy_notice_needs_no_session(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    del owner
    client = await new_client()
    assert (await client.get("/privacy")).status_code == 200
    assert (await client.get(f"{AUTH}/me")).status_code == 401


async def test_the_address_never_reaches_the_application_log(
    db_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The M2 done-check, log half. ``caplog`` sees each record BEFORE the redaction
    processor runs, so this fails if any code on the capture path so much as hands the
    address to a logger -- a stricter test than checking the redacted output.

    The edge log is Caddy's, and is filtered in the Caddyfile (docs/ERRORS.md E26).
    """
    link = await ch.create_link()
    caplog.set_level("DEBUG")

    await ch.visit(
        db_client,
        link.slug,
        peer=ch.VISITOR_IP,
        headers={"X-Forwarded-For": ch.VISITOR_IP, "CF-Connecting-IP": ch.VISITOR_IP},
    )
    page = await ch.visit(db_client, link.slug, peer=ch.VISITOR_IP)
    await db_client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json={"screen": {"w": -1}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )

    assert caplog.records, "the capture path should log something"
    assert ch.VISITOR_IP not in caplog.text
