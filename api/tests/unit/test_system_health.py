"""System Health's host readings, against fake /proc and /sys trees (F10.AC2, F10.AC15).

Neither the dev box nor GCP exposes a temperature sensor (SPEC section 11 row 23), so the
path that reads a real one is proved here, against a /sys that has one.
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


def test_a_thermal_zone_is_read_in_celsius(tmp_path: Path) -> None:
    _write(tmp_path / "class/thermal/thermal_zone0/temp", "47500\n")
    _write(tmp_path / "class/thermal/thermal_zone0/type", "x86_pkg_temp\n")
    reading = system.read_temperature(tmp_path)
    assert (reading.celsius, reading.sensor, reading.reason) == (47.5, "x86_pkg_temp", None)


def test_the_hottest_sensor_wins_across_thermal_and_hwmon(tmp_path: Path) -> None:
    _write(tmp_path / "class/thermal/thermal_zone0/temp", "41000")
    _write(tmp_path / "class/hwmon/hwmon2/name", "coretemp")
    _write(tmp_path / "class/hwmon/hwmon2/temp1_input", "63000")
    _write(tmp_path / "class/hwmon/hwmon2/temp1_label", "Package id 0")
    reading = system.read_temperature(tmp_path)
    assert reading.celsius == 63.0
    assert reading.sensor == "coretemp Package id 0"


def test_nonsense_readings_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "class/thermal/thermal_zone0/temp", "-273000")  # disconnected
    _write(tmp_path / "class/thermal/thermal_zone1/temp", "not a number")
    reading = system.read_temperature(tmp_path)
    assert reading.celsius is None
    assert reading.reason == system.NO_SENSOR


def test_no_sensor_is_na_with_the_reason(tmp_path: Path) -> None:
    _write(tmp_path / "class/thermal/cooling_device0/type", "Processor")  # what Docker Desktop has
    reading = system.read_temperature(tmp_path)
    assert reading.celsius is None
    assert reading.reason is not None and "no temperature sensor" in reading.reason


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
