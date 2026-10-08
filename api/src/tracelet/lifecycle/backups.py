"""Backups and the restore check (ADR-0014, ADR-0022, F10.AC11, F12.AC9-AC12).

**A backup** is ``pg_dump`` in custom format, run as ``tracelet_maint`` inside a
repeatable-read snapshot this module exports and counts first -- so ``row_counts`` are the
dump's own counts, not counts taken a moment later. The file is written as ``.partial``
and renamed only once complete and checksummed, so a half-written dump is never listed.

**A restore check** restores a backup into the scratch database ``tracelet_verify``, made
from the template ``tracelet_verify_template`` (``tl db-setup``), and passes only if every
table's count equals the backup's. The scratch database is dropped whatever happens.

**Rotation** keeps the newest backup of each of the last 7 local days and of each of the
last 4 ISO weeks, and always the newest; other files are deleted and their rows become
``pruned``. Rows are never deleted.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import shutil
import uuid
import zoneinfo
from collections.abc import Coroutine, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from tracelet.config import Settings, get_settings
from tracelet.db.dml import execute_rowcount
from tracelet.db.engine import session_scope
from tracelet.errors import BackupUnavailable, LifecycleJobRunning, MaintenanceUnavailable
from tracelet.lifecycle.maint import libpq_env, maint_connection, maint_url
from tracelet.lifecycle.models import Backup, BackupKind, BackupStatus, RestoreCheck, RestoreStatus
from tracelet.lifecycle.retention import local_boundary

log = structlog.get_logger(__name__)

SCRATCH_DB: Final = "tracelet_verify"
TEMPLATE_DB: Final = "tracelet_verify_template"
# A running row older than this was left by a worker that died mid-job.
ABANDONED_AFTER: Final = dt.timedelta(hours=2)
# Free disk needed beyond the database's own size, for a dump or a scratch restore.
DISK_MARGIN_BYTES: Final = 200 * 1024 * 1024
# The scheduled backup is retried at most this many times a night after a failure.
NIGHTLY_ATTEMPTS: Final = 3
ERROR_TAIL: Final = 600


class JobFailedError(Exception):
    """A backup or restore check failed for a reason the owner should read."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _run(argv: Sequence[str], env: dict[str, str]) -> None:
    """Run a client binary; raise with the tail of its stderr. The password is in the
    environment, so neither argv nor stderr carries it."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), **env},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip()[-ERROR_TAIL:]
        msg = f"{Path(argv[0]).name} exited {process.returncode}: {tail or 'no output'}"
        raise JobFailedError(msg)


async def _tables(conn: AsyncConnection) -> list[str]:
    """The database's own tables. Extension-owned ones (PostGIS's ``spatial_ref_sys``) are
    left out: the scratch database has them from its template, not from the dump."""
    rows = await conn.execute(
        text(
            """
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                              AND d.objid = c.oid AND d.deptype = 'e')
            ORDER BY 1
            """
        )
    )
    return [str(name) for (name,) in rows]


async def _counts(conn: AsyncConnection, tables: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        sql = text(f"SELECT count(*) FROM {quoted}")  # noqa: S608 -- name from pg_class, quoted
        counts[table] = int((await conn.execute(sql)).scalar_one())
    return counts


async def _database_size(settings: Settings) -> int:
    async with maint_connection(settings) as conn, conn.begin():
        return int(
            (await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
        )


def _need_disk(path: Path, needed: int, what: str) -> None:
    free = shutil.disk_usage(path).free
    if free < needed:
        msg = (
            f"Not enough free disk for {what}: {free // 2**20} MB free, "
            f"{needed // 2**20} MB needed."
        )
        raise JobFailedError(msg)


async def recover_abandoned() -> int:
    """Running rows a dead worker left behind become failed, so they stop blocking."""
    cutoff = _now() - ABANDONED_AFTER
    abandoned = "Abandoned: the worker stopped."
    async with session_scope() as db:
        backups = await execute_rowcount(
            db,
            update(Backup)
            .where(Backup.status == BackupStatus.RUNNING, Backup.started_at < cutoff)
            .values(status=BackupStatus.FAILED, finished_at=_now(), error=abandoned),
        )
        checks = await execute_rowcount(
            db,
            update(RestoreCheck)
            .where(RestoreCheck.status == RestoreStatus.RUNNING, RestoreCheck.started_at < cutoff)
            .values(status=RestoreStatus.FAILED, finished_at=_now(), error=abandoned),
        )
    return backups + checks


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


async def begin_backup(kind: BackupKind, requested_by: uuid.UUID | None) -> uuid.UUID:
    """The ``running`` row, or ``LifecycleJobRunning`` if one exists (a unique index)."""
    await recover_abandoned()
    try:
        async with session_scope() as db:
            row = Backup(kind=kind, status=BackupStatus.RUNNING, requested_by=requested_by)
            db.add(row)
            await db.flush()
            return row.id
    except IntegrityError as exc:
        raise LifecycleJobRunning("A backup is already running.") from exc


async def run_backup(backup_id: uuid.UUID, settings: Settings | None = None) -> Backup:
    """Dump, count, checksum, rename, then rotate. Marks the row ok or failed."""
    settings = settings or get_settings()
    directory = settings.backup_dir
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    async with session_scope() as db:
        started = await db.get(Backup, backup_id)
        if started is None:
            msg = f"No backup {backup_id}."
            raise JobFailedError(msg)
        kind = started.kind
    name = f"{stamp}-{kind.value}.dump"
    partial = directory / f"{name}.partial"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _need_disk(directory, await _database_size(settings) + DISK_MARGIN_BYTES, "a backup")
        async with maint_connection(settings) as conn:
            conn = await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                snapshot = str(
                    (await conn.execute(text("SELECT pg_export_snapshot()"))).scalar_one()
                )
                counts = await _counts(conn, await _tables(conn))
                # The snapshot lives as long as this transaction: pg_dump must finish first.
                await _run(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--compress=6",
                        f"--snapshot={snapshot}",
                        f"--file={partial}",
                    ],
                    libpq_env(settings=settings),
                )
        checksum = await asyncio.to_thread(_sha256, partial)
        size = partial.stat().st_size
        partial.rename(directory / name)
    except (JobFailedError, MaintenanceUnavailable, OSError) as exc:
        partial.unlink(missing_ok=True)
        return await _finish_backup(backup_id, error=str(exc))
    except Exception:
        partial.unlink(missing_ok=True)
        await _finish_backup(backup_id, error="Unexpected error; see the server log.")
        raise
    row = await _finish_backup(
        backup_id, file_name=name, size_bytes=size, sha256=checksum, row_counts=counts
    )
    await prune(settings)
    log.info("backup_done", kind=kind.value, size_bytes=size, tables=len(counts))
    return row


async def _finish_backup(
    backup_id: uuid.UUID,
    *,
    error: str | None = None,
    file_name: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    row_counts: dict[str, int] | None = None,
) -> Backup:
    async with session_scope() as db:
        row = await db.get(Backup, backup_id)
        assert row is not None
        row.finished_at = _now()
        if error is None:
            row.status = BackupStatus.OK
            row.file_name, row.size_bytes, row.sha256 = file_name, size_bytes, sha256
            row.row_counts = dict(row_counts or {})
        else:
            row.status, row.error = BackupStatus.FAILED, error[:ERROR_TAIL]
            log.warning("backup_failed", backup_id=str(backup_id), error=error[:200])
        await db.flush()
        return row


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Kept:
    id: uuid.UUID
    started_at: dt.datetime


def to_keep(
    backups: Sequence[Kept], *, now: dt.datetime, tz: str, daily: int, weekly: int
) -> set[uuid.UUID]:
    """Newest of each of the last ``daily`` local days and ``weekly`` ISO weeks, and the
    newest of all (F12.AC9). Pure, so the rule is tested without files."""
    if not backups:
        return set()
    zone = zoneinfo.ZoneInfo(tz)
    today = now.astimezone(zone).date()
    days = {today - dt.timedelta(days=n) for n in range(daily)}
    this_week = today.isocalendar()
    weeks = {(today - dt.timedelta(weeks=n)).isocalendar()[:2] for n in range(weekly)} | {
        this_week[:2]
    }
    keep: set[uuid.UUID] = {max(backups, key=lambda b: b.started_at).id}
    newest_day: dict[dt.date, Kept] = {}
    newest_week: dict[tuple[int, int], Kept] = {}
    for b in backups:
        local = b.started_at.astimezone(zone).date()
        if local in days and (
            local not in newest_day or b.started_at > newest_day[local].started_at
        ):
            newest_day[local] = b
        week = local.isocalendar()[:2]
        if week in weeks and (
            week not in newest_week or b.started_at > newest_week[week].started_at
        ):
            newest_week[week] = b
    keep |= {b.id for b in newest_day.values()} | {b.id for b in newest_week.values()}
    return keep


async def prune(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    async with session_scope() as db:
        rows = list(
            (await db.execute(select(Backup).where(Backup.status == BackupStatus.OK))).scalars()
        )
        keep = to_keep(
            [Kept(r.id, r.started_at) for r in rows],
            now=_now(),
            tz=settings.reporting_tz,
            daily=settings.backup_daily_keep,
            weekly=settings.backup_weekly_keep,
        )
        pruned = 0
        for row in rows:
            if row.id in keep:
                continue
            if row.file_name:
                (settings.backup_dir / row.file_name).unlink(missing_ok=True)
            row.status = BackupStatus.PRUNED
            pruned += 1
    if pruned:
        log.info("backups_pruned", count=pruned)
    return pruned


def file_of(row: Backup, settings: Settings) -> Path:
    if row.status is not BackupStatus.OK or not row.file_name:
        msg = f"This backup is {row.status.value}; only a completed backup has a file."
        raise BackupUnavailable(msg)
    path = settings.backup_dir / row.file_name
    if not path.is_file():
        raise BackupUnavailable("The backup's file is missing from the disk.")
    return path


# ---------------------------------------------------------------------------
# Restore check
# ---------------------------------------------------------------------------


async def newest_ok_backup() -> uuid.UUID | None:
    async with session_scope() as db:
        return (
            await db.execute(
                select(Backup.id)
                .where(Backup.status == BackupStatus.OK)
                .order_by(Backup.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def begin_restore_check(
    backup_id: uuid.UUID, kind: BackupKind, requested_by: uuid.UUID | None
) -> int:
    await recover_abandoned()
    try:
        async with session_scope() as db:
            row = RestoreCheck(
                backup_id=backup_id,
                kind=kind,
                status=RestoreStatus.RUNNING,
                requested_by=requested_by,
            )
            db.add(row)
            await db.flush()
            return row.id
    except IntegrityError as exc:
        raise LifecycleJobRunning("A restore check is already running.") from exc


def restorable(listing: str, tables: set[str]) -> str:
    """The ``pg_restore --list`` entries to restore into the scratch database.

    Extensions already exist there (from the template), and only their owner may comment
    on them. Their tables' data -- PostGIS's ``spatial_ref_sys`` -- is the template's too,
    and the maintenance role may not write it. So: no extension entries, and data only for
    the tables the backup counted, which are exactly the database's own.
    """
    kept: list[str] = []
    for line in listing.splitlines():
        if " EXTENSION " in line:
            continue
        if " TABLE DATA " in line:
            parts = line.split(" TABLE DATA ", 1)[1].split()
            if len(parts) < 2 or parts[1] not in tables:
                continue
        kept.append(line)
    return "\n".join(kept) + "\n"


async def _scratch(settings: Settings, *, create: bool) -> None:
    async with maint_connection(settings, autocommit=True) as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        if create:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :t AND datistemplate"),
                    {"t": TEMPLATE_DB},
                )
            ).scalar_one_or_none()
            if exists is None:
                msg = (
                    f"The template database {TEMPLATE_DB} is missing. Run ./scripts/tl db-setup "
                    "once, as described in ADR-0022."
                )
                raise JobFailedError(msg)
            await conn.execute(text(f"CREATE DATABASE {SCRATCH_DB} TEMPLATE {TEMPLATE_DB}"))


async def _restored_counts(settings: Settings, tables: Iterable[str]) -> dict[str, int]:
    url = maint_url(settings).set(database=SCRATCH_DB)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn, conn.begin():
            present = set(await _tables(conn))
            return await _counts(conn, [t for t in tables if t in present])
    finally:
        await engine.dispose()


async def run_restore_check(check_id: int, settings: Settings | None = None) -> RestoreCheck:
    settings = settings or get_settings()
    async with session_scope() as db:
        check = await db.get(RestoreCheck, check_id)
        assert check is not None
        backup = await db.get(Backup, check.backup_id)
        assert backup is not None
    mismatches: dict[str, dict[str, int | None]] = {}
    error: str | None = None
    try:
        path = file_of(backup, settings)
        _need_disk(
            path.parent, await _database_size(settings) + DISK_MARGIN_BYTES, "a restore check"
        )
        await _scratch(settings, create=True)
        listing = path.with_name(f"{path.name}.list")
        try:
            await _run(
                ["pg_restore", "--list", f"--file={listing}", str(path)],
                libpq_env(settings=settings),
            )
            kept = restorable(listing.read_text(encoding="utf-8"), set(backup.row_counts or {}))
            listing.write_text(kept, encoding="utf-8")
            await _run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    f"--use-list={listing}",
                    f"--dbname={SCRATCH_DB}",
                    str(path),
                ],
                libpq_env(SCRATCH_DB, settings=settings),
            )
        finally:
            listing.unlink(missing_ok=True)
        expected = {k: int(v) for k, v in (backup.row_counts or {}).items()}
        restored = await _restored_counts(settings, expected)
        for table, count in expected.items():
            if restored.get(table) != count:
                mismatches[table] = {"expected": count, "restored": restored.get(table)}
        if not expected:
            error = "The backup records no row counts to check against."
    except BackupUnavailable as exc:
        error = exc.detail or str(exc)
    except (JobFailedError, MaintenanceUnavailable, OSError) as exc:
        error = str(exc)
    finally:
        try:
            await _scratch(settings, create=False)
        except Exception as exc:  # noqa: BLE001 -- the result matters more; the next run drops it
            log.warning("scratch_drop_failed", error_type=type(exc).__name__)
    passed = error is None and not mismatches
    async with session_scope() as db:
        row = await db.get(RestoreCheck, check_id)
        assert row is not None
        row.status = RestoreStatus.PASSED if passed else RestoreStatus.FAILED
        row.mismatches = mismatches or None
        row.error = error[:ERROR_TAIL] if error else ("Row counts differ." if mismatches else None)
        row.finished_at = _now()
        await db.flush()
    log.info(
        "restore_check_done", passed=passed, backup_id=str(backup.id), mismatched=len(mismatches)
    )
    return row


# ---------------------------------------------------------------------------
# Scheduled: nightly backup, monthly restore check
# ---------------------------------------------------------------------------


def month_boundary(now: dt.datetime, hour: int, tz: str) -> dt.datetime:
    """The most recent first-of-the-month ``hour:00`` in ``tz``, as UTC."""
    zone = zoneinfo.ZoneInfo(tz)
    local = now.astimezone(zone)
    first = local.replace(day=1, hour=hour, minute=0, second=0, microsecond=0)
    if first > local:
        first = (first - dt.timedelta(days=1)).replace(day=1)
    return first.astimezone(dt.UTC)


def nightly_due(tonight: Sequence[BackupStatus]) -> bool:
    """Whether tonight's backup still has to run, given tonight's scheduled attempts.

    Done if one completed -- even if rotation has since pruned it in favour of a newer
    backup the same day (ERRORS E70: it then ran again every 15 minutes) -- or one is
    running; given up after ``NIGHTLY_ATTEMPTS`` failures.
    """
    done = {BackupStatus.OK, BackupStatus.PRUNED, BackupStatus.RUNNING}
    failures = sum(1 for status in tonight if status is BackupStatus.FAILED)
    return not done.intersection(tonight) and failures < NIGHTLY_ATTEMPTS


async def run_scheduled_once(settings: Settings | None = None) -> str | None:
    """Every 15 minutes: the nightly backup if due, then the monthly restore check if due."""
    settings = settings or get_settings()
    if settings.maint_database_url is None:
        return None
    await recover_abandoned()
    now = _now()
    night = local_boundary(now, settings.backup_hour, settings.reporting_tz)
    async with session_scope() as db:
        tonight = list(
            (
                await db.execute(
                    select(Backup.status).where(
                        Backup.kind == BackupKind.SCHEDULED, Backup.started_at >= night
                    )
                )
            ).scalars()
        )
    if nightly_due(tonight):
        try:
            backup_id = await begin_backup(BackupKind.SCHEDULED, None)
        except LifecycleJobRunning:
            return None
        await run_backup(backup_id, settings)
        return "backup"
    month = month_boundary(now, (settings.backup_hour + 1) % 24, settings.reporting_tz)
    if now < month:
        return None
    async with session_scope() as db:
        done = (
            await db.execute(
                select(RestoreCheck.id).where(
                    RestoreCheck.kind == BackupKind.SCHEDULED, RestoreCheck.started_at >= month
                )
            )
        ).first()
    newest = await newest_ok_backup()
    if done is not None or newest is None:
        return None
    try:
        check_id = await begin_restore_check(newest, BackupKind.SCHEDULED, None)
    except LifecycleJobRunning:
        return None
    await run_restore_check(check_id, settings)
    return "restore_check"


_TASKS: set[asyncio.Task[object]] = set()


def in_background(job: Coroutine[Any, Any, object], name: str) -> asyncio.Task[object]:
    """A manual backup or restore check, after its ``running`` row exists (``202``)."""
    task: asyncio.Task[object] = asyncio.create_task(job, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
