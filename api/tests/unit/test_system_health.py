"""System Health's host readings, against fake /proc trees (F10.AC15).

Temperature is gone: F10.AC2 was withdrawn (SPEC section 11 row 40).
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tracelet.config import Settings
from tracelet.health import system
from tracelet.ratelimit import gcra, registry


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_without_the_host_mount_the_figures_say_they_are_the_containers(tmp_path: Path) -> None:
    scope, reason = system.use_host_procfs(Settings(host_proc_path=tmp_path / "absent"))
    assert scope == "container"
    assert reason is not None and "not mounted" in reason
    _write(tmp_path / "proc/stat", "cpu  1 2 3 4\n")
    scope, reason = system.use_host_procfs(Settings(host_proc_path=tmp_path / "proc"))
    assert (scope, reason) == ("host", None)
    system.use_host_procfs(Settings(host_proc_path=tmp_path / "absent"))  # back to /proc


class _Times(NamedTuple):
    """The shape of psutil's cpu_times(): seconds per state."""

    user: float
    nice: float
    system: float
    idle: float
    iowait: float


def test_cpu_busy_share_between_two_samples() -> None:
    # 100 seconds elapsed, 50 of them idle or waiting on I/O.
    before, after = _Times(10, 0, 10, 80, 0), _Times(40, 0, 30, 120, 10)
    assert system._busy_percent(before, after) == 50.0
    assert system._busy_percent(before, before) == 0.0


def test_rate_limit_overrides_parse_and_ignore_the_malformed() -> None:
    parsed = gcra.parse_overrides(
        {
            "cap_m": {"per_period": 60, "period_seconds": 60, "burst": 20},
            "login_id": {"per_period": "x"},
            "hp": {"per_period": 0, "period_seconds": 60, "burst": 1},
        }
    )
    assert set(parsed) == {"cap_m"}
    assert parsed["cap_m"].per_period == 60 and parsed["cap_m"].burst == 20


def test_an_outbound_limit_cannot_exceed_the_third_partys_terms() -> None:
    assert registry.problem("out_nominatim_m", 60, 60, 1) is None  # exactly 1 a second
    assert registry.problem("out_nominatim_m", 61, 60, 1) is not None
    assert registry.problem("out_ipwhois_d", 1001, 86400, 20) is not None
    assert registry.problem("nope", 1, 1, 1) is not None
    assert registry.problem("cap_m", 0, 60, 1) is not None
