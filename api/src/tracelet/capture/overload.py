"""Overload: when a worker answers a capture from memory (ADR-0028, CLAUDE.md invariant 1).

M9's load test on the e2-micro left 500 visitors un-redirected: a capture needed the
database, nothing bounded it below Caddy's 10 s, and the redirects shared an event loop
with the scheduler on a throttled vCPU. This module decides, per worker, when a capture
must not touch the database at all, and keeps what those captures would have written.

**Overloaded** when any of three signals says so, with hysteresis so it does not flap:

* **event-loop lag** -- a ticker sleeps 100 ms and measures how late it wakes, smoothed.
  A starved or blocked loop is exactly what memory pressure cannot see;
* **captures in flight** in this worker, including ones finishing behind a deadline;
* **memory pressure**, the existing signal (``health/pressure.py``).

While overloaded a capture is answered from the warm link cache and its minimal
``rate_limited`` row goes into a bounded buffer, written in batches once the worker
recovers; past the bound visits are only counted. The scheduler pauses every job but the
outbox and the sweeper (``worker/scheduler.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from sqlalchemy import insert

from tracelet.capture.models import Visit, VisitStage, uuid7
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.health import pressure

log = structlog.get_logger(__name__)

TICK_S: Final = 0.1
# Exponential smoothing of the lag: one late wake-up is not overload, a run of them is.
LAG_ALPHA: Final = 0.3
FLUSH_EVERY_S: Final = 1.0
FLUSH_BATCH: Final = 500


@dataclass
class _State:
    overloaded: bool = False
    lag_ms: float = 0.0
    inflight: int = 0
    reason: str = ""
    since: float | None = None
    answered: int = 0  # captures answered from memory in the current episode
    total_answered: int = 0
    buffer: deque[dict[str, Any]] = field(default_factory=deque)
    dropped: int = 0  # buffer full: counted only


_state = _State()
# The shed buffer's bound per worker (TRACELET_OVERLOAD_BUFFER), set by configure().
_buffer_cap = 5000


def _decide(settings: Settings, mem: bool) -> tuple[bool, str]:
    lag_limit = float(settings.overload_lag_ms)
    cap = settings.overload_inflight
    if not _state.overloaded:
        if _state.lag_ms >= lag_limit:
            return True, "event_loop_lag"
        if _state.inflight > cap:
            return True, "captures_in_flight"
        if mem:
            return True, "memory_pressure"
        return False, ""
    # Recover only when every signal is well clear: half the lag, half the captures.
    if _state.lag_ms < lag_limit / 2 and _state.inflight <= cap // 2 and not mem:
        return False, ""
    return True, _state.reason


def evaluate(settings: Settings) -> bool:
    """This worker's verdict now. Cheap: the memory signal re-reads /proc at most every
    two seconds. Never raises."""
    try:
        mem = pressure.shedding(settings)
    except Exception:  # noqa: BLE001 - the verdict must not fail a capture
        mem = False
    was = _state.overloaded
    _state.overloaded, _state.reason = _decide(settings, mem)
    if _state.overloaded and not was:
        _state.since, _state.answered = time.monotonic(), 0
        log.warning(
            "capture_overload_started",
            reason=_state.reason,
            lag_ms=round(_state.lag_ms),
            inflight=_state.inflight,
        )
    elif was and not _state.overloaded:
        log.warning(
            "capture_overload_stopped",
            answered_from_memory=_state.answered,
            seconds=round(time.monotonic() - (_state.since or time.monotonic())),
            buffered=len(_state.buffer),
            dropped=_state.dropped,
        )
        _state.since = None
    return _state.overloaded


def is_overloaded() -> bool:
    """The last verdict, without re-evaluating: for the scheduler's per-tick check."""
    return _state.overloaded


# --- captures in flight ------------------------------------------------------------------


def started() -> None:
    _state.inflight += 1


def finished(_: object = None) -> None:
    """Also a task done-callback: a capture finishing behind its deadline still counts
    until it completes."""
    _state.inflight = max(0, _state.inflight - 1)


# --- answered from memory ----------------------------------------------------------------


def record_shed(
    *, link_id: uuid.UUID, ip_prefix: str | None, trace_id: str | None, classifier_version: str
) -> None:
    """Keep the minimal ``rate_limited`` row the capture would have written (F11.AC3).

    The same columns as the database path, nothing a client supplied, never a raw IP
    (CLAUDE.md invariant 4), and the arrival time, because the row is written later.
    """
    _state.answered += 1
    _state.total_answered += 1
    if len(_state.buffer) >= _buffer_cap:
        _state.dropped += 1
        return
    now = dt.datetime.now(dt.UTC)
    _state.buffer.append(
        {
            "id": uuid7(),
            "link_id": link_id,
            "occurred_at": now,
            "finalized_at": now,
            "stage": VisitStage.RATE_LIMITED,
            "trace_id": trace_id,
            "classifier_version": classifier_version,
            "ip_prefix": ip_prefix,
        }
    )


def configure(settings: Settings) -> None:
    global _buffer_cap  # noqa: PLW0603 - per-process configuration, set once at startup
    _buffer_cap = settings.overload_buffer


async def flush(*, limit: int = FLUSH_BATCH) -> int:
    """Write up to ``limit`` buffered rows in one statement. Returns how many.

    On failure the rows go back to the front of the buffer, to be tried again; the next
    attempt waits for the next tick, so a database that is down is not hammered.
    """
    if not _state.buffer:
        return 0
    batch = [_state.buffer.popleft() for _ in range(min(limit, len(_state.buffer)))]
    try:
        async with session_scope() as db:
            await db.execute(insert(Visit), batch)
    except Exception as exc:  # noqa: BLE001 - kept and retried, never lost to an error here
        _state.buffer.extendleft(reversed(batch))
        log.error("capture_shed_flush_failed", rows=len(batch), error_type=type(exc).__name__)
        return 0
    return len(batch)


# --- background tasks, one set per worker ------------------------------------------------


async def _lag_ticker(settings: Settings, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        before = loop.time()
        await asyncio.sleep(TICK_S)
        late_ms = max(0.0, (loop.time() - before - TICK_S) * 1000)
        _state.lag_ms = (1 - LAG_ALPHA) * _state.lag_ms + LAG_ALPHA * late_ms
        evaluate(settings)


async def _flusher(stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=FLUSH_EVERY_S)
        if not _state.overloaded:
            await flush()


class Monitor:
    """The ticker and the flusher for one worker process (started in the app lifespan)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        configure(self._settings)
        self._tasks = [
            asyncio.create_task(_lag_ticker(self._settings, self._stop), name="overload:lag"),
            asyncio.create_task(_flusher(self._stop), name="overload:flush"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        # Best effort: what is still buffered goes now, or is reported lost.
        with contextlib.suppress(Exception):
            while _state.buffer and await flush():
                pass
        if _state.buffer or _state.dropped:
            log.warning(
                "capture_shed_lost_at_shutdown", buffered=len(_state.buffer), dropped=_state.dropped
            )


# --- for the degradation banner and tests ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Overload:
    overloaded: bool
    reason: str
    lag_ms: float
    inflight: int
    answered: int
    buffered: int
    dropped: int


def current() -> Overload:
    return Overload(
        overloaded=_state.overloaded,
        reason=_state.reason,
        lag_ms=_state.lag_ms,
        inflight=_state.inflight,
        answered=_state.answered,
        buffered=len(_state.buffer),
        dropped=_state.dropped,
    )


def reset_for_tests() -> None:
    global _state, _buffer_cap  # noqa: PLW0603 - the per-process state, by design
    _state = _State()
    _buffer_cap = 5000
