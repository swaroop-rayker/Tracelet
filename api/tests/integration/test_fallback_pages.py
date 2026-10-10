"""Link changes rewrite Caddy's fallback pages at once (ADR-0029, F1.AC4).

Through the real links API and database: creating a link writes its page, deactivating
or archiving it removes the page, and making a link the default writes the bare path's
page. The pages are rewritten after the edit has committed, so they read what was saved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration import capture_helpers as ch
from tests.integration.helpers import SignedIn
from tracelet.capture import fallback_pages
from tracelet.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("public_dns")]

LINKS = "/api/v1/links"


@pytest.fixture
def pages(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def integration_settings(integration_settings: Settings, pages: Path) -> Settings:
    return integration_settings.model_copy(update={"fallback_dir": pages})


async def _create(
    owner: SignedIn, destination: str = "https://example.com/landing"
) -> dict[str, str]:
    response = await owner.client.post(
        LINKS,
        json={"slug": ch.new_slug(), "label": "Fallback test", "destination_url": destination},
        headers=owner.headers(),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_a_new_link_gets_a_page(owner: SignedIn, pages: Path) -> None:
    link = await _create(owner)
    page = pages / f"{link['slug']}.html"
    assert page.exists()
    assert 'url=https://example.com/landing"' in page.read_text()
    assert (pages / fallback_pages.UNAVAILABLE_PAGE).exists()


async def test_an_edited_destination_is_rewritten(owner: SignedIn, pages: Path) -> None:
    link = await _create(owner)
    response = await owner.client.patch(
        f"{LINKS}/{link['id']}",
        json={"destination_url": "https://example.org/moved"},
        headers=owner.headers(),
    )
    assert response.status_code == 200, response.text
    assert "example.org/moved" in (pages / f"{link['slug']}.html").read_text()


async def test_a_deactivated_link_stops_redirecting_at_once(owner: SignedIn, pages: Path) -> None:
    link = await _create(owner)
    response = await owner.client.patch(
        f"{LINKS}/{link['id']}", json={"is_active": False}, headers=owner.headers()
    )
    assert response.status_code == 200, response.text
    assert not (pages / f"{link['slug']}.html").exists()


async def test_an_archived_link_stops_redirecting_at_once(owner: SignedIn, pages: Path) -> None:
    link = await _create(owner)
    response = await owner.client.post(f"{LINKS}/{link['id']}/archive", headers=owner.headers())
    assert response.status_code in (200, 204), response.text
    assert not (pages / f"{link['slug']}.html").exists()


async def test_the_default_link_has_the_bare_path_page(owner: SignedIn, pages: Path) -> None:
    link = await _create(owner, "https://example.com/home")
    response = await owner.client.post(f"{LINKS}/{link['id']}/default", headers=owner.headers())
    assert response.status_code == 200, response.text
    assert "example.com/home" in (pages / fallback_pages.DEFAULT_PAGE).read_text()
