"""The geo-database installer against real I/O (F10.AC4, M3 done-check).

Real filesystem, real subprocess validation, real PostgreSQL; only HTTP is replaced, by
an ``httpx.MockTransport``, so the suite never downloads anything.

Every spec here has a throwaway ``t-`` name. The development database is also where the
real databases are recorded, and a test must never supersede a real install.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import gzip
import hashlib
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference.geodb import catalog
from tracelet.inference.geodb.catalog import BY_NAME, DatabaseSpec, Download
from tracelet.inference.geodb.installer import current_path, install
from tracelet.inference.models import GeoDatabase, GeoDbStatus

pytestmark = pytest.mark.integration

URL = "https://geo.example.test/file"
FALLBACK_URL = "https://geo.example.test/last-month"
TOKEN = "tok-never-shown-4417"


def admin1_file(codes: int) -> bytes:
    lines = [f"IN.{i}\tPlace {i}\tPlace {i}\t{1000 + i}" for i in range(20)]
    lines += [f"XX.{i}\tRegion {i}\tRegion {i}\t{9000 + i}" for i in range(codes - 20)]
    return ("\n".join(lines) + "\n").encode()


GOOD = admin1_file(3_100)  # includes IN.19, as the validator requires
TRUNCATED = admin1_file(40)


@pytest.fixture(autouse=True)
async def _purge_test_rows(db_app: object) -> AsyncIterator[None]:
    del db_app
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geo_databases WHERE name LIKE 't-%'"))


@pytest.fixture
def settings(integration_settings: Settings, tmp_path: Path) -> Settings:
    return integration_settings.model_copy(update={"geo_data_dir": tmp_path})


@pytest.fixture
def spec_for(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., DatabaseSpec]]:
    def make(
        base: str = "geonames-admin1",
        *,
        download: Download | None = None,
        **changes: object,
    ) -> DatabaseSpec:
        name = f"t-{base}-{uuid.uuid4().hex[:8]}"
        spec = dataclasses.replace(BY_NAME[base], name=name, **changes)  # type: ignore[arg-type]  # test helper forwards typed fields
        chosen = download or Download(URL)
        monkeypatch.setitem(catalog._URLS, name, lambda _settings, _today: chosen)
        return spec

    yield make


def serving(responses: dict[str, httpx.Response]) -> tuple[httpx.AsyncClient, list[str]]:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return responses.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requested


async def rows(name: str) -> list[GeoDatabase]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(GeoDatabase).where(GeoDatabase.name == name).order_by(GeoDatabase.id)
                )
            ).scalars()
        )


# ---------------------------------------------------------------------------


async def test_a_good_download_is_installed_and_recorded(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    spec = spec_for()
    client, _ = serving({URL: httpx.Response(200, content=GOOD)})

    result = await install(spec, settings, client=client)

    assert result.status == "installed"
    assert current_path(settings, spec).read_bytes() == GOOD
    (row,) = await rows(spec.name)
    assert row.status is GeoDbStatus.INSTALLED
    assert row.sha256 == hashlib.sha256(GOOD).hexdigest()
    assert not (settings.geo_data_dir / ".staging").exists() or not any(
        (settings.geo_data_dir / ".staging").iterdir()
    ), "staging is cleaned up"


async def test_an_identical_download_changes_nothing(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    spec = spec_for()
    client, _ = serving({URL: httpx.Response(200, content=GOOD)})
    await install(spec, settings, client=client)

    again = await install(spec, settings, client=client)

    assert again.status == "unchanged"
    assert [r.status for r in await rows(spec.name)] == [GeoDbStatus.INSTALLED]


async def test_the_same_bytes_under_a_newer_date_take_the_date(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    """ERRORS E71: the Tor list is republished with a new Last-Modified and the same bytes.
    The serving copy is then the latest release, and must say so, or the release check calls
    it outdated for ever (SPEC section 11 row 24)."""
    spec = spec_for()
    first = "Wed, 07 Oct 2026 06:00:00 GMT"
    later = "Wed, 07 Oct 2026 07:00:00 GMT"
    client, _ = serving({URL: httpx.Response(200, content=GOOD, headers={"last-modified": first})})
    await install(spec, settings, client=client)
    client, _ = serving({URL: httpx.Response(200, content=GOOD, headers={"last-modified": later})})

    assert (await install(spec, settings, client=client)).status == "unchanged"
    (row,) = await rows(spec.name)
    assert row.released_at is not None and row.released_at.hour == 7


async def test_a_corrupt_update_leaves_the_previous_version_serving(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    """The M3 done-check, and F10.AC4: validation fails, `current` never moves."""
    spec = spec_for()
    good, _ = serving({URL: httpx.Response(200, content=GOOD)})
    await install(spec, settings, client=good)
    before = current_path(settings, spec).resolve()

    bad, _ = serving({URL: httpx.Response(200, content=TRUNCATED)})
    result = await install(spec, settings, client=bad)

    assert result.status == "failed"
    assert result.detail is not None and result.detail.startswith("validation failed")
    assert current_path(settings, spec).resolve() == before
    assert current_path(settings, spec).read_bytes() == GOOD
    statuses = [r.status for r in await rows(spec.name)]
    assert statuses == [GeoDbStatus.INSTALLED, GeoDbStatus.FAILED]


async def test_a_broken_archive_is_refused(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    spec = spec_for("dbip-asn-lite", download=Download(URL, version="2026-09"))
    client, _ = serving({URL: httpx.Response(200, content=b"this is not gzip")})

    result = await install(spec, settings, client=client)

    assert (result.status, result.detail) == ("failed", "archive is corrupt (BadGzipFile)")
    assert not current_path(settings, spec).exists()


async def test_a_download_over_its_size_cap_is_abandoned(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    spec = spec_for(max_bytes=1024)
    client, _ = serving({URL: httpx.Response(200, content=GOOD)})

    result = await install(spec, settings, client=client)

    assert result.status == "failed"
    assert result.detail is not None and "cap" in result.detail


async def test_a_month_not_yet_published_falls_back_to_the_last(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    fallback = Download(FALLBACK_URL, version="2026-08")
    spec = spec_for("dbip-asn-lite", download=Download(URL, version="2026-09", fallback=fallback))
    client, requested = serving(
        {FALLBACK_URL: httpx.Response(200, content=gzip.compress(b"still not a database"))}
    )

    await install(spec, settings, client=client)

    assert requested[:2] == [URL, FALLBACK_URL]


async def test_a_checksum_mismatch_is_refused(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    sha_url = f"{URL}.sha256"
    spec = spec_for(download=Download(URL, sha256_url=sha_url))
    client, _ = serving(
        {
            URL: httpx.Response(200, content=GOOD),
            sha_url: httpx.Response(200, text=f"{'0' * 64}  admin1CodesASCII.txt"),
        }
    )

    result = await install(spec, settings, client=client)

    assert (result.status, result.detail) == (
        "failed",
        "checksum mismatch against the vendor's published SHA-256",
    )


async def test_a_token_in_the_url_never_reaches_the_record(
    settings: Settings, spec_for: Callable[..., DatabaseSpec]
) -> None:
    """F12.AC13: two vendors carry their token in the URL."""
    url = f"{URL}?token={TOKEN}"
    spec = spec_for(download=Download(url))
    client, _ = serving({url: httpx.Response(500)})

    result = await install(spec, settings, client=client)

    assert result.detail == "download refused (HTTP 500)"
    async with session_scope() as db:
        dumped = (
            await db.execute(
                text(
                    "SELECT string_agg(to_jsonb(g)::text, ' ') FROM geo_databases g WHERE name = :n"
                ),
                {"n": spec.name},
            )
        ).scalar_one()
    assert TOKEN not in str(dumped)


async def test_an_unconfigured_database_is_skipped(
    integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = dataclasses.replace(BY_NAME["ipinfo-lite"], name="t-ipinfo-unconfigured")
    monkeypatch.setitem(catalog._URLS, spec.name, lambda _settings, _today: None)

    result = await install(spec, integration_settings, today=dt.date(2026, 9, 29))

    assert (result.status, result.detail) == ("skipped", "credentials_not_configured")
    assert await rows(spec.name) == []
