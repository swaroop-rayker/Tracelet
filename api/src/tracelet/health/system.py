"""Host metrics for System Health (F10.AC1, F10.AC2, F10.AC15, RW-5).

**The host, not the container.** ``docker-compose.yml`` mounts the host's ``/proc`` and
``/sys`` read-only at ``/host/proc`` and ``/host/sys``; psutil reads ``/proc`` from
``PROCFS_PATH``, so CPU, memory, swap, load and uptime are the VM's. Where the mount is
absent (tests, a bare ``docker run``), the figures are the container's own and the
response says so -- a number that looks like the host's but is not would be worse than
none.

**Temperature** is read from ``/sys/class/thermal`` and ``/sys/class/hwmon`` by these few
lines, because psutil's sensor reader hard-codes ``/sys``. Neither a GCP e2-micro nor
Docker Desktop's VM exposes a sensor (SPEC section 11 row 23), so the usual answer is
``None`` with the reason.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import psutil

from tracelet.config import Settings

CPU_SAMPLE_S: Final = 0.25
NO_SENSOR: Final = (
    "This host exposes no temperature sensor. Cloud VMs and Docker Desktop's VM do not (RW-5)."
)


@dataclass(frozen=True, slots=True)
class Usage:
    used: int
    total: int

    @property
    def percent(self) -> float:
        return round(100.0 * self.used / self.total, 1) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class Temperature:
    celsius: float | None
    sensor: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class HostSample:
    scope: str  # "host" or "container"
    scope_reason: str | None
    cpu_percent: float
    cpu_count: int
    load: tuple[float, float, float]
    memory: Usage
    swap: Usage
    disk: Usage
    disk_path: str
    uptime_seconds: int
    temperature: Temperature
    sampled_at: dt.datetime


def use_host_procfs(settings: Settings) -> tuple[str, str | None]:
    if (settings.host_proc_path / "stat").is_file():
        psutil.PROCFS_PATH = str(settings.host_proc_path)
        return "host", None
    psutil.PROCFS_PATH = "/proc"
    return "container", (
        f"{settings.host_proc_path} is not mounted, so these figures are the container's, "
        "not the host's (F10.AC15)."
    )


def _busy_percent(before: tuple[float, ...], after: tuple[float, ...]) -> float:
    """CPU busy share between two ``cpu_times()`` samples (a named tuple of seconds)."""

    def total(t: tuple[float, ...]) -> float:
        return float(sum(t))

    idle_fields = ("idle", "iowait")
    idle_before = sum(getattr(before, f, 0.0) for f in idle_fields)
    idle_after = sum(getattr(after, f, 0.0) for f in idle_fields)
    elapsed = total(after) - total(before)
    if elapsed <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1 - (idle_after - idle_before) / elapsed))), 1)


def _loadavg(proc: Path) -> tuple[float, float, float]:
    parts = (proc / "loadavg").read_text(encoding="ascii").split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def read_temperature(sys_root: Path) -> Temperature:
    """The hottest sensor the host exposes, in Celsius, or ``None`` with the reason."""
    readings: list[tuple[float, str]] = []
    for zone in sorted((sys_root / "class" / "thermal").glob("thermal_zone*")):
        reading = _millidegrees(zone / "temp")
        if reading is not None:
            label = _text(zone / "type") or zone.name
            readings.append((reading, label))
    for chip in sorted((sys_root / "class" / "hwmon").glob("hwmon*")):
        name = _text(chip / "name") or chip.name
        for sensor in sorted(chip.glob("temp*_input")):
            reading = _millidegrees(sensor)
            if reading is not None:
                own = _text(sensor.with_name(sensor.name.replace("_input", "_label")))
                readings.append((reading, f"{name} {own or sensor.name.split('_')[0]}"))
    if not readings:
        return Temperature(celsius=None, sensor=None, reason=NO_SENSOR)
    celsius, label = max(readings)
    return Temperature(celsius=round(celsius, 1), sensor=label, reason=None)


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _millidegrees(path: Path) -> float | None:
    raw = _text(path)
    try:
        value = int(raw) / 1000 if raw is not None else None
    except ValueError:
        return None
    # A disconnected sensor reports nonsense; nothing real is below -40 or above 150 C.
    return value if value is not None and -40.0 <= value <= 150.0 else None


async def sample(settings: Settings) -> HostSample:
    scope, reason = use_host_procfs(settings)
    proc = Path(psutil.PROCFS_PATH)
    before = psutil.cpu_times()
    await asyncio.sleep(CPU_SAMPLE_S)
    after = psutil.cpu_times()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_path = settings.backup_dir if settings.backup_dir.exists() else Path("/")
    disk = psutil.disk_usage(str(disk_path))
    sys_root = settings.host_sys_path if scope == "host" else Path("/sys")
    return HostSample(
        scope=scope,
        scope_reason=reason,
        cpu_percent=_busy_percent(before, after),
        cpu_count=psutil.cpu_count() or 1,
        load=_loadavg(proc),
        memory=Usage(used=memory.total - memory.available, total=memory.total),
        swap=Usage(used=swap.used, total=swap.total),
        disk=Usage(used=disk.used, total=disk.total),
        disk_path=str(disk_path),
        uptime_seconds=int(dt.datetime.now(dt.UTC).timestamp() - psutil.boot_time()),
        temperature=read_temperature(sys_root),
        sampled_at=dt.datetime.now(dt.UTC),
    )
