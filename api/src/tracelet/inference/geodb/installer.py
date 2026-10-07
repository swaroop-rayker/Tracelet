"""Download, verify, validate and atomically install one offline database (F10.AC4).

Layout under ``Settings.geo_data_dir``::

    <name>/<version>/<file>     one directory per installed version
    <name>/current              symlink -> <version>; what the readers open
    .staging/                   in-flight downloads, removed on success and failure

The order is the guarantee. Stream to staging with a size cap and a running SHA-256;
check the vendor's published checksum where there is one; unpack; validate in a
memory-capped subprocess; **then** swap ``current`` with an atomic rename; **then** record
it. Every failure before the rename leaves ``current`` exactly where it was, which is how
"a deliberately corrupted download leaves the previous version serving" holds (M3).

Nothing here logs a download URL: two vendors carry their token in it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import hashlib
import json
import shutil
import sys
import tarfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx
import structlog
from sqlalchemy import delete, select, update

from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference.geodb.catalog import DatabaseSpec, Download
from tracelet.inference.models import GeoDatabase, GeoDbStatus

log = structlog.get_logger(__name__)

CHUNK: Final = 1024 * 1024
VALIDATE_TIMEOUT_S: Final = 300.0
USER_AGENT: Final = "Tracelet/0.1 geo-database updater"


class InstallError(Exception):
    """A step failed. The message is safe to store and show: never a URL or a token."""


@dataclass(frozen=True, slots=True)
class InstallResult:
    name: str
    status: str  # installed | unchanged | skipped | failed
    version: str | None = None
    detail: str | None = None


def current_path(settings: Settings, spec: DatabaseSpec) -> Path:
    """What the readers open. May not exist: the database is simply not installed."""
    return settings.geo_data_dir / spec.name / "current" / spec.installed_as


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class Progress:
    """Writes an attempt's phase and download progress to its row (SPEC section 11 row 24),
    so either worker can report it. Throttled: at most once a second or every 2 %."""

    def __init__(self, row_id: int) -> None:
        self.row_id = row_id
        self._written_at = 0.0
        self._written_fraction = -1.0

    async def phase(self, name: str) -> None:
        await self._write(phase=name, progress_bytes=None, total_bytes=None)

    async def bytes(self, done: int, total: int | None) -> None:
        now = time.monotonic()
        fraction = done / total if total else 0.0
        if now - self._written_at < 1.0 and fraction - self._written_fraction < 0.02:
            return
        self._written_at, self._written_fraction = now, fraction
        await self._write(phase="downloading", progress_bytes=done, total_bytes=total)

    async def _write(self, **values: object) -> None:
        try:
            async with session_scope() as db:
                await db.execute(
                    update(GeoDatabase).where(GeoDatabase.id == self.row_id).values(**values)
                )
        except Exception as exc:  # noqa: BLE001 -- progress is a courtesy; never fail the install
            log.debug("geo_progress_not_written", error_type=type(exc).__name__)


async def _fetch(
    client: httpx.AsyncClient,
    dl: Download,
    dest: Path,
    max_bytes: int,
    progress: Progress | None = None,
) -> tuple[Download, str, dt.datetime | None]:
    """Stream ``dl`` to ``dest``. Returns what was actually fetched, its SHA-256, and the
    server's Last-Modified. On a 404, tries ``dl.fallback`` once."""
    attempts = [dl] if dl.fallback is None else [dl, dl.fallback]
    for attempt in attempts:
        digest = hashlib.sha256()
        size = 0
        async with client.stream("GET", attempt.url, auth=attempt.auth) as response:
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise InstallError(f"download refused (HTTP {response.status_code})")
            length = response.headers.get("content-length")
            total = int(length) if length and length.isdigit() else None
            with dest.open("wb") as out:
                async for chunk in response.aiter_bytes(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise InstallError(f"larger than the {max_bytes // 1_048_576} MB cap")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress is not None:
                        await progress.bytes(size, total)
            released = http_date(response.headers.get("last-modified"))
        return attempt, digest.hexdigest(), released
    raise InstallError("not published (HTTP 404)")


def http_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime  # noqa: PLC0415 - rarely used

        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


async def _published_sha256(client: httpx.AsyncClient, dl: Download) -> str | None:
    if dl.sha256_url is None:
        return None
    response = await client.get(dl.sha256_url, auth=dl.auth)
    if response.status_code != 200:
        raise InstallError(f"checksum unavailable (HTTP {response.status_code})")
    # MaxMind's format: "<hex>  <filename>".
    return response.text.split()[0].strip().lower()


def _unpack(spec: DatabaseSpec, archive: Path, into: Path) -> Path:
    """Stream the wanted member out of ``archive``. Never loads it whole."""
    target = into / spec.installed_as
    try:
        if spec.packing == "none":
            shutil.move(str(archive), target)
        elif spec.packing == "gz":
            with gzip.open(archive, "rb") as gz, target.open("wb") as out:
                shutil.copyfileobj(gz, out, CHUNK)
        elif spec.packing == "tar.gz":
            with tarfile.open(archive, "r:gz") as tar:
                member = next(
                    (
                        m
                        for m in tar.getmembers()
                        if m.isfile()
                        and (m.name == spec.member or m.name.endswith(f"/{spec.member}"))
                    ),
                    None,
                )
                if member is None:
                    raise InstallError(f"{spec.member} not found in the archive")
                src = tar.extractfile(member)
                if src is None:
                    raise InstallError(f"{spec.member} could not be read from the archive")
                with src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
        elif spec.packing == "zip":
            with zipfile.ZipFile(archive) as zf:
                name = next(
                    (n for n in zf.namelist() if n == spec.member or n.endswith(f"/{spec.member}")),
                    None,
                )
                if name is None:
                    raise InstallError(f"{spec.member} not found in the archive")
                with zf.open(name) as zipped, target.open("wb") as out:
                    shutil.copyfileobj(zipped, out, CHUNK)
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, gzip.BadGzipFile) as exc:
        raise InstallError(f"archive is corrupt ({type(exc).__name__})") from exc
    return target


async def _validate(spec: DatabaseSpec, path: Path) -> dict[str, object]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tracelet.inference.geodb.validate",
        spec.kind,
        str(path),
        spec.database_type or "",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=VALIDATE_TIMEOUT_S)
    except TimeoutError as exc:
        proc.kill()
        raise InstallError("validation timed out") from exc
    try:
        verdict: dict[str, object] = json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        # Killed by the memory cap, typically: no verdict at all.
        raise InstallError(f"validation crashed (exit {proc.returncode})") from exc
    if proc.returncode != 0 or not verdict.get("ok"):
        raise InstallError(f"validation failed: {verdict.get('error', 'no reason given')}")
    return verdict


def _swap(spec_dir: Path, version_dir: Path) -> None:
    """Point ``current`` at ``version_dir`` atomically: a reader sees the old file or the
    new one, never neither."""
    tmp = spec_dir / f".current-{uuid.uuid4().hex}"
    tmp.symlink_to(version_dir.name, target_is_directory=True)
    tmp.replace(spec_dir / "current")


def _prune(spec_dir: Path, keep: set[str]) -> None:
    """Keep the serving version and the one before it (a manual rollback path)."""
    for child in spec_dir.iterdir():
        if child.is_dir() and not child.is_symlink() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


# ---------------------------------------------------------------------------
# The whole install
# ---------------------------------------------------------------------------


async def _record_start(spec: DatabaseSpec) -> int:
    async with session_scope() as db:
        row = GeoDatabase(
            name=spec.name,
            status=GeoDbStatus.DOWNLOADING,
            staleness_threshold_days=spec.staleness_days,
            last_check_at=dt.datetime.now(dt.UTC),
        )
        db.add(row)
        await db.flush()
        return row.id


async def _record_failure(row_id: int, error: str) -> None:
    async with session_scope() as db:
        await db.execute(
            update(GeoDatabase)
            .where(GeoDatabase.id == row_id)
            .values(status=GeoDbStatus.FAILED, last_error=error[:500], phase=None)
        )


async def install(
    spec: DatabaseSpec,
    settings: Settings,
    *,
    today: dt.date | None = None,
    client: httpx.AsyncClient | None = None,
) -> InstallResult:
    """Install the current release of ``spec``. Never raises; the result says what happened."""
    dl = spec.download(settings, today or dt.datetime.now(dt.UTC).date())
    if dl is None:
        return InstallResult(spec.name, "skipped", detail="credentials_not_configured")

    root = settings.geo_data_dir
    staging = root / ".staging" / f"{spec.name}-{uuid.uuid4().hex}"
    spec_dir = root / spec.name
    row_id = await _record_start(spec)
    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        staging.mkdir(parents=True)
        archive = staging / "download"
        progress = Progress(row_id)
        await progress.phase("downloading")
        fetched, sha256, released = await _fetch(http, dl, archive, spec.max_bytes, progress)
        await progress.phase("verifying")
        expected = await _published_sha256(http, fetched)
        if expected is not None and expected != sha256:
            raise InstallError("checksum mismatch against the vendor's published SHA-256")

        async with session_scope() as db:
            serving = (
                await db.execute(
                    select(GeoDatabase).where(
                        GeoDatabase.name == spec.name, GeoDatabase.status == GeoDbStatus.INSTALLED
                    )
                )
            ).scalar_one_or_none()
        if (
            serving is not None
            and serving.sha256 == sha256
            and current_path(settings, spec).exists()
        ):
            async with session_scope() as db:
                await db.execute(
                    update(GeoDatabase)
                    .where(GeoDatabase.id == serving.id)
                    # The vendor republished the same bytes under a newer date (the Tor list
                    # does, every few minutes): the serving copy *is* the latest, so take
                    # the date, or the release check would call it outdated for ever (E71).
                    .values(
                        last_check_at=dt.datetime.now(dt.UTC),
                        released_at=released or serving.released_at,
                    )
                )
                # Nothing was installed, so the attempt leaves no row behind.
                await db.execute(delete(GeoDatabase).where(GeoDatabase.id == row_id))
            return InstallResult(spec.name, "unchanged", serving.version)

        version = fetched.version or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        version_dir_name = f"{version}-{sha256[:8]}"
        unpacked_dir = staging / "unpacked"
        unpacked_dir.mkdir()
        await progress.phase("unpacking")
        unpacked = await asyncio.to_thread(_unpack, spec, archive, unpacked_dir)
        archive.unlink(missing_ok=True)
        await progress.phase("validating")
        await _validate(spec, unpacked)
        await progress.phase("installing")

        spec_dir.mkdir(parents=True, exist_ok=True)
        version_dir = spec_dir / version_dir_name
        if version_dir.exists():
            shutil.rmtree(version_dir)
        shutil.move(str(unpacked_dir), version_dir)
        previous = (
            (spec_dir / "current").resolve().name if (spec_dir / "current").exists() else None
        )
        _swap(spec_dir, version_dir)

        size = (version_dir / spec.installed_as).stat().st_size
        async with session_scope() as db:
            # Clear the old installed row first: the partial unique index allows one.
            await db.execute(
                update(GeoDatabase)
                .where(GeoDatabase.name == spec.name, GeoDatabase.status == GeoDbStatus.INSTALLED)
                .values(status=GeoDbStatus.STALE)
            )
            await db.execute(
                update(GeoDatabase)
                .where(GeoDatabase.id == row_id)
                .values(
                    status=GeoDbStatus.INSTALLED,
                    version=version,
                    released_at=released,
                    installed_at=dt.datetime.now(dt.UTC),
                    file_path=str(version_dir / spec.installed_as),
                    sha256=sha256,
                    size_bytes=size,
                    last_error=None,
                    phase=None,
                    progress_bytes=None,
                    total_bytes=None,
                )
            )
        _prune(spec_dir, {version_dir_name, previous or ""})
        log.info("geo_database_installed", database=spec.name, version=version, size_bytes=size)
        return InstallResult(spec.name, "installed", version)
    except InstallError as exc:
        await _record_failure(row_id, str(exc))
        log.warning("geo_database_install_failed", database=spec.name, reason=str(exc))
        return InstallResult(spec.name, "failed", detail=str(exc))
    except (httpx.HTTPError, OSError) as exc:
        reason = f"{type(exc).__name__} during download or install"
        await _record_failure(row_id, reason)
        log.warning("geo_database_install_failed", database=spec.name, reason=reason)
        return InstallResult(spec.name, "failed", detail=reason)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if owns_client:
            await http.aclose()
