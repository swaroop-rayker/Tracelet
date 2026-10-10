"""The redirect pages Caddy serves when the api cannot answer (ADR-0029)."""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

from tracelet.capture import fallback_pages
from tracelet.capture.service import LinkSnapshot


def _link(
    slug: str, destination: str = "https://example.com/landing", **kw: object
) -> LinkSnapshot:
    base = LinkSnapshot(
        id=uuid.uuid4(),
        slug=slug,
        destination_url=destination,
        interstitial_ms=700,
        ask_location=False,
        is_live=True,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]  # test overrides by name


def test_a_page_redirects_to_the_destination_and_offers_a_link() -> None:
    page = fallback_pages.render("https://example.com/a?b=1&c=2")
    assert 'content="0;url=https://example.com/a?b=1&amp;c=2"' in page
    assert 'href="https://example.com/a?b=1&amp;c=2"' in page
    assert '<link rel="icon" href="data:,">' in page  # E63, E81: no favicon fetch


def test_a_destination_cannot_break_out_of_the_attribute() -> None:
    page = fallback_pages.render('https://example.com/"><script>alert(1)</script>')
    assert "<script>" not in page
    assert "&quot;&gt;&lt;script&gt;" in page


def test_sync_writes_live_links_the_default_and_the_unavailable_page(tmp_path: Path) -> None:
    live, off = _link("spring-sale"), _link("old-promo", is_live=False)
    changed = fallback_pages.sync(tmp_path, [live, off], live)
    names = sorted(p.name for p in tmp_path.glob("*.html"))
    assert names == ["_default.html", "_unavailable.html", "spring-sale.html"]
    assert changed == 3
    assert "url=https://example.com/landing" in (tmp_path / "spring-sale.html").read_text()


def test_sync_removes_a_page_whose_link_is_no_longer_live(tmp_path: Path) -> None:
    link = _link("spring-sale")
    fallback_pages.sync(tmp_path, [link], None)
    fallback_pages.sync(tmp_path, [dataclasses.replace(link, is_live=False)], None)
    assert not (tmp_path / "spring-sale.html").exists()  # F1.AC4: stops redirecting at once


def test_sync_rewrites_only_what_changed(tmp_path: Path) -> None:
    link = _link("spring-sale")
    fallback_pages.sync(tmp_path, [link], None)
    assert fallback_pages.sync(tmp_path, [link], None) == 0
    edited = dataclasses.replace(link, destination_url="https://example.org/new")
    assert fallback_pages.sync(tmp_path, [edited], None) == 1
    assert "example.org/new" in (tmp_path / "spring-sale.html").read_text()


def test_an_inactive_default_has_no_page(tmp_path: Path) -> None:
    default = _link("home-link", is_live=False)
    fallback_pages.sync(tmp_path, [default], default)
    assert not (tmp_path / fallback_pages.DEFAULT_PAGE).exists()


def test_a_missing_directory_never_raises(tmp_path: Path) -> None:
    assert fallback_pages.sync(tmp_path / "absent", [_link("spring-sale")], None) == 0


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    fallback_pages.sync(tmp_path, [_link("spring-sale")], None)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
