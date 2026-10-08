"""Memory pressure, and when to shed capture work (F15.AC6, RISKS R6, ADR-0010 amendment).

On a 1 GB box with 2 GB of swap on a network disk, swapping is a cliff: once it starts,
every request slows sharply. F15.AC6 asks that requests be shed at L2 before the kernel's
OOM killer has to choose. The signal is the kernel's own measure of thrashing --
**pressure stall information**, ``/proc/pressure/memory`` "some avg10": the share of the
last ten seconds in which some task was stalled waiting for memory. Where the kernel has no
PSI, the fallback is the swap-in rate from ``/proc/vmstat``.

Shedding starts at ``TRACELET_SHED_MEMORY_PRESSURE`` percent (20) and stops below half of
it, so it does not flap. Each worker reads the host's file at most every two seconds --
one small read, no background task, no shared state to keep in step. While shedding, a
capture is handled as a rate-limited one: the visitor is redirected at once and a minimal
``rate_limited`` row records it (ADR-0010) -- the visitor's journey never waits on memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import structlog

from tracelet.config import Settings

log = structlog.get_logger(__name__)

CHECK_EVERY_S: Final = 2.0


@dataclass
class _State:
    shedding: bool = False
    checked_at: float = float("-inf")
    signal: str = "none"
    value: float = 0.0
    swapin_pages: int | None = None
    swapin_at: float = 0.0
    shed_since: float | None = None
    shed_count: int = 0
    reasons: list[str] = field(default_factory=list)


_state = _State()


def _proc(settings: Settings) -> Path:
    return (
        settings.host_proc_path if (settings.host_proc_path / "stat").is_file() else Path("/proc")
    )


def read_psi(proc: Path) -> float | None:
    """``some avg10`` from ``pressure/memory``, or ``None`` where the kernel has no PSI."""
    try:
        for line in (proc / "pressure" / "memory").read_text(encoding="ascii").splitlines():
            if line.startswith("some "):
                for part in line.split()[1:]:
                    key, _, value = part.partition("=")
                    if key == "avg10":
                        return float(value)
    except (OSError, ValueError):
        return None
    return None


def read_swapin(proc: Path) -> int | None:
    try:
        for line in (proc / "vmstat").read_text(encoding="ascii").splitlines():
            if line.startswith("pswpin "):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _decide(on: bool, value: float, threshold: float) -> bool:
    """Hysteresis: start at the threshold, stop below half of it."""
    return value >= threshold / 2 if on else value >= threshold


def shedding(settings: Settings, *, now: float | None = None) -> bool:
    """Whether capture work should be shed right now. Never raises."""
    now = time.monotonic() if now is None else now
    if settings.shed_memory_pressure <= 0 or now - _state.checked_at < CHECK_EVERY_S:
        return _state.shedding and settings.shed_memory_pressure > 0
    _state.checked_at = now
    proc = _proc(settings)
    was = _state.shedding
    psi = read_psi(proc)
    if psi is not None:
        _state.signal, _state.value = "memory_pressure", psi
        _state.shedding = _decide(was, psi, settings.shed_memory_pressure)
    else:
        pages = read_swapin(proc)
        rate = 0.0
        if pages is not None and _state.swapin_pages is not None and now > _state.swapin_at:
            rate = max(0.0, (pages - _state.swapin_pages) / (now - _state.swapin_at))
        _state.swapin_pages, _state.swapin_at = pages, now
        _state.signal, _state.value = "swap_in_pages_per_s", rate
        _state.shedding = _decide(was, rate, float(settings.shed_swapin_pages_per_s))
    if _state.shedding and not was:
        _state.shed_since = now
        log.warning("capture_shedding_started", signal=_state.signal, value=_state.value)
    elif was and not _state.shedding:
        log.warning(
            "capture_shedding_stopped",
            shed=_state.shed_count,
            seconds=round(now - (_state.shed_since or now)),
        )
        _state.shed_since, _state.shed_count = None, 0
    return _state.shedding


def note_shed() -> None:
    _state.shed_count += 1


@dataclass(frozen=True, slots=True)
class Pressure:
    shedding: bool
    signal: str
    value: float
    shed_count: int


def current(settings: Settings) -> Pressure:
    """For the degradation banner: this worker's view, refreshed if stale."""
    shedding(settings)
    return Pressure(_state.shedding, _state.signal, _state.value, _state.shed_count)


def reset_for_tests() -> None:
    global _state  # noqa: PLW0603 -- the per-process state, by design
    _state = _State()
