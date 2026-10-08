"""Load shedding's signal and its hysteresis (F15.AC6, health/pressure.py)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tracelet.config import Settings
from tracelet.health import pressure

PSI = "some avg10={some:.2f} avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1\n"


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    pressure.reset_for_tests()
    yield
    pressure.reset_for_tests()


def _host(tmp_path: Path, *, some: float | None = None, swapin: int | None = None) -> Settings:
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text("cpu  1 2 3 4\n", encoding="ascii")
    psi = proc / "pressure" / "memory"
    if some is None:
        psi.unlink(missing_ok=True)
    else:
        psi.write_text(PSI.format(some=some), encoding="ascii")
    if swapin is not None:
        (proc / "vmstat").write_text(f"pgpgin 1\npswpin {swapin}\npswpout 0\n", encoding="ascii")
    return Settings(host_proc_path=proc, shed_memory_pressure=20.0, shed_swapin_pages_per_s=100)


def test_psi_is_read_from_the_some_line(tmp_path: Path) -> None:
    settings = _host(tmp_path, some=37.5)
    assert pressure.read_psi(settings.host_proc_path) == 37.5


def test_shedding_starts_at_the_threshold_and_stops_below_half(tmp_path: Path) -> None:
    clock = 100.0
    assert pressure.shedding(_host(tmp_path, some=19.9), now=clock) is False
    clock += 3
    assert pressure.shedding(_host(tmp_path, some=20.0), now=clock) is True
    clock += 3
    assert pressure.shedding(_host(tmp_path, some=12.0), now=clock) is True  # still above 10
    clock += 3
    assert pressure.shedding(_host(tmp_path, some=9.9), now=clock) is False


def test_the_file_is_read_at_most_every_two_seconds(tmp_path: Path) -> None:
    assert pressure.shedding(_host(tmp_path, some=50.0), now=10.0) is True
    # Pressure has gone, but within two seconds the last answer stands.
    assert pressure.shedding(_host(tmp_path, some=0.0), now=11.0) is True
    assert pressure.shedding(_host(tmp_path, some=0.0), now=12.5) is False


def test_without_psi_the_swap_in_rate_decides(tmp_path: Path) -> None:
    assert pressure.shedding(_host(tmp_path, swapin=1_000), now=10.0) is False  # first sample
    assert pressure.shedding(_host(tmp_path, swapin=1_100), now=13.0) is False  # 33 a second
    assert pressure.shedding(_host(tmp_path, swapin=1_500), now=16.0) is True  # 133 a second
    assert pressure.current(_host(tmp_path, swapin=1_500)).signal == "swap_in_pages_per_s"


def test_zero_turns_shedding_off(tmp_path: Path) -> None:
    settings = _host(tmp_path, some=99.0).model_copy(update={"shed_memory_pressure": 0.0})
    assert pressure.shedding(settings, now=10.0) is False
