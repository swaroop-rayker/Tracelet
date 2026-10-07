"""What is degraded right now, for the banner on every dashboard page (F10.AC14, NFR3.AC3).

Each condition names what is wrong **and what still works**, because the answer to "is
it broken?" is nearly always "the redirect is fine; this part is not" (CLAUDE.md invariant
1). Severity: ``critical`` needs the owner soon (a failed backup, a full disk); ``warning``
is degraded but self-correcting or tolerable; ``notice`` is advice (download a backup).

Everything is read from shared state -- the database, the host -- so both workers give
the same answer. Cheap enough to poll: no CPU sample, one query per area.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import psutil
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.config import Settings
from tracelet.health import databases
from tracelet.health.system import use_host_procfs
from tracelet.inference.outbound import BREAKER_KEY_PREFIX
from tracelet.lifecycle.models import Backup, BackupStatus, RestoreCheck, RestoreStatus
from tracelet.notify.outbox import Outbox, OutboxStatus

# The restore check is monthly; this long without a passed one means it is not happening.
RESTORE_OVERDUE: Final = dt.timedelta(days=35)
# The backup is nightly; this long without one means it is not happening.
BACKUP_OVERDUE: Final = dt.timedelta(hours=36)

BREAKER_NAMES: Final = {
    "ipwhois": "ipwho.is (IP lookup)",
    "nominatim": "Nominatim (street address)",
}


@dataclass(frozen=True, slots=True)
class Condition:
    key: str
    severity: str  # critical, warning, notice
    title: str
    detail: str
    still_works: str


def _disk_memory(settings: Settings) -> list[Condition]:
    out: list[Condition] = []
    use_host_procfs(settings)
    path = str(settings.backup_dir) if settings.backup_dir.exists() else "/"
    disk = psutil.disk_usage(path)
    if disk.percent >= settings.disk_warn_percent:
        critical = disk.percent >= settings.disk_critical_percent
        out.append(
            Condition(
                key="disk",
                severity="critical" if critical else "warning",
                title=f"Disk {disk.percent:.0f} % full",
                detail=(
                    f"Above the {settings.disk_critical_percent if critical else settings.disk_warn_percent} % "
                    "threshold. Free space by lowering retention and purging, or by deleting "
                    "old backups after downloading one."
                ),
                still_works="Visits are still captured and redirected; backups and purges go first.",
            )
        )
    swap = psutil.swap_memory()
    if swap.total and swap.percent >= settings.swap_warn_percent:
        out.append(
            Condition(
                key="swap",
                severity="warning",
                title=f"Swap {swap.percent:.0f} % used",
                detail="Memory is short and the host is swapping; responses may slow down.",
                still_works="Requests are shed at the rate limiter before the kernel kills anything.",
            )
        )
    return out


async def _geo(db: AsyncSession, settings: Settings) -> list[Condition]:
    out: list[Condition] = []
    for state in await databases.states(db, settings):
        if state.verdict in ("stale", "missing") and state.configured:
            out.append(
                Condition(
                    key=f"geodb:{state.name}",
                    severity="warning",
                    title=f"Geo database {state.name} is {state.verdict}",
                    detail=(
                        f"Last attempt: {state.last_attempt.status}"
                        + (f" -- {state.last_attempt.error}" if state.last_attempt.error else "")
                        if state.last_attempt
                        else "It has never been installed."
                    ),
                    still_works="Location is inferred from the other sources.",
                )
            )
    return out


async def _breakers(db: AsyncSession) -> list[Condition]:
    rows = await db.execute(
        text("SELECT key, tat FROM rate_limit_buckets WHERE key LIKE :p AND tat > now()"),
        {"p": f"{BREAKER_KEY_PREFIX}%"},
    )
    out: list[Condition] = []
    for key, until in rows:
        name = str(key).removeprefix(BREAKER_KEY_PREFIX)
        out.append(
            Condition(
                key=f"breaker:{name}",
                severity="warning",
                title=f"{BREAKER_NAMES.get(name, name)} is not answering",
                detail=f"Its circuit breaker is open until {until:%H:%M} UTC, then it is tried again.",
                still_works="Inference continues on the remaining sources.",
            )
        )
    return out


async def _outbox(db: AsyncSession) -> list[Condition]:
    dead = (
        await db.execute(select(func.count()).where(Outbox.status == OutboxStatus.DEAD))
    ).scalar_one()
    if not dead:
        return []
    return [
        Condition(
            key="outbox",
            severity="warning",
            title=f"{dead} Telegram alert{'s' if dead != 1 else ''} could not be delivered",
            detail="Dead-lettered after every retry. Retry them from Alerts once Telegram answers.",
            still_works="Nothing is lost: each alert stays queued until retried.",
        )
    ]


async def _backups(db: AsyncSession, settings: Settings) -> list[Condition]:
    if settings.maint_database_url is None:
        return [
            Condition(
                key="backups",
                severity="critical",
                title="Backups are not set up",
                detail="TRACELET_MAINT_DATABASE_URL is not set, so nothing is backed up or purged.",
                still_works="Everything else.",
            )
        ]
    now = dt.datetime.now(dt.UTC)
    out: list[Condition] = []
    newest = (
        await db.execute(
            select(Backup)
            .where(Backup.status != BackupStatus.RUNNING)
            .order_by(Backup.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    # max() of nothing is NULL, whatever the column's type says.
    newest_ok: dt.datetime | None = (
        await db.execute(
            select(func.max(Backup.started_at)).where(
                Backup.status.in_([BackupStatus.OK, BackupStatus.PRUNED])
            )
        )
    ).scalar_one()
    if newest is not None and newest.status is BackupStatus.FAILED:
        out.append(
            Condition(
                key="backups",
                severity="critical",
                title="The last backup failed",
                detail=newest.error or "See System Health.",
                still_works="Earlier backups are still on disk.",
            )
        )
    elif newest_ok is None or now - newest_ok > BACKUP_OVERDUE:
        out.append(
            Condition(
                key="backups",
                severity="warning",
                title="No recent backup",
                detail="No backup has completed in the last 36 hours.",
                still_works="Everything else.",
            )
        )
    check = (
        await db.execute(
            select(RestoreCheck)
            .where(RestoreCheck.status != RestoreStatus.RUNNING)
            .order_by(RestoreCheck.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if check is not None and check.status is RestoreStatus.FAILED:
        out.append(
            Condition(
                key="restore",
                severity="critical",
                title="The last restore check failed",
                detail=check.error or "The restored counts did not match.",
                still_works="Backups continue; the failure says what to fix.",
            )
        )
    elif newest_ok is not None and (check is None or now - check.started_at > RESTORE_OVERDUE):
        out.append(
            Condition(
                key="restore",
                severity="warning",
                title="Backups are not verified",
                detail="No restore check has passed in the last 35 days. An unverified backup "
                "is not yet a backup (F12.AC10).",
                still_works="Backups continue.",
            )
        )
    last_download = (await db.execute(select(func.max(Backup.last_downloaded_at)))).scalar_one()
    reminder = dt.timedelta(days=settings.backup_download_reminder_days)
    if newest_ok is not None and (last_download is None or now - last_download > reminder):
        out.append(
            Condition(
                key="download",
                severity="notice",
                title="Download a backup",
                detail=(
                    "No backup has been downloaded in "
                    f"{settings.backup_download_reminder_days} days. The download is the only "
                    "copy off this machine (RISKS R11)."
                ),
                still_works="Backups are kept on this machine.",
            )
        )
    return out


async def conditions(db: AsyncSession, settings: Settings) -> list[Condition]:
    found = (
        _disk_memory(settings)
        + await _backups(db, settings)
        + await _outbox(db)
        + await _breakers(db)
        + await _geo(db, settings)
    )
    rank = {"critical": 0, "warning": 1, "notice": 2}
    return sorted(found, key=lambda c: rank[c.severity])
