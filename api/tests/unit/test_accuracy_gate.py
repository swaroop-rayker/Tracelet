"""The CI accuracy gate (F14.AC12, ADR-0024): it passes on the code defaults, fails on a
deliberately regressed threshold in either direction, and the fixture keeps nothing it
should not.

The committed synthetic fixture is what CI always runs; these tests pin the three facts
its builder (``tests/fixtures/accuracy/build_synthetic.py``) was written to hold.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tracelet.accuracy import fixture
from tracelet.accuracy.metrics import Report, score
from tracelet.accuracy.replay import Case, Truth
from tracelet.cli.main import main
from tracelet.inference.config import DEFAULT_CONFIG, InferenceConfig, Thresholds
from tracelet.inference.types import AsnInfo, Candidate, GeoLevel, InferenceSource

SYNTHETIC = Path(__file__).parents[1] / "fixtures" / "accuracy" / "synthetic.json"


def _check(config: InferenceConfig) -> Report:
    loaded = fixture.read(SYNTHETIC)
    return score(
        loaded.cases,
        config,
        settings_version=None,
        inference_version="test",
        classifier_version="test",
    )


def _with_admin1_threshold(value: float) -> InferenceConfig:
    thresholds = DEFAULT_CONFIG.thresholds.model_copy(update={"admin1": value})
    return DEFAULT_CONFIG.model_copy(update={"thresholds": Thresholds.model_validate(thresholds)})


def _status(report: Report, target_id: str) -> str:
    return next(t.status for t in report.targets if t.id == target_id)


def test_the_synthetic_fixture_passes_under_the_code_defaults() -> None:
    report = _check(DEFAULT_CONFIG)
    assert report.passed is True
    assert report.label_count >= 30


def test_a_lowered_threshold_emits_a_wrong_state_and_fails_precision() -> None:
    report = _check(_with_admin1_threshold(0.30))
    assert report.passed is False
    assert _status(report, "admin1.strict_precision") == "missed"


def test_a_raised_threshold_abstains_and_fails_coverage() -> None:
    report = _check(_with_admin1_threshold(0.99))
    assert report.passed is False
    assert _status(report, "admin1.strict_coverage") == "missed"


def test_targets_stated_only_in_words_are_reported_not_gated() -> None:
    report = _check(DEFAULT_CONFIG)
    words = {t.id: t for t in report.targets if not t.gated}
    assert set(words) == {
        "country.strict_coverage",
        "city.strict_coverage",
        "consented.city.advisory_accuracy",
    }
    assert all(t.status == "reported" and t.target is None for t in words.values())


def test_nothing_measurable_is_neither_a_pass_nor_a_fail() -> None:
    empty = score(
        [], DEFAULT_CONFIG, settings_version=None, inference_version="t", classifier_version="t"
    )
    assert empty.passed is None
    assert {t.status for t in empty.targets if t.gated} == {"unmeasured"}


# --- the CLI, as CI runs it -----------------------------------------------------


def test_cli_check_exits_zero_on_pass(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["accuracy", "check", "--fixture", str(SYNTHETIC)]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_cli_check_exits_one_on_a_regressed_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    regressed = tmp_path / "regressed.json"
    regressed.write_text(json.dumps(_with_admin1_threshold(0.30).model_dump(mode="json")))
    code = main(["accuracy", "check", "--fixture", str(SYNTHETIC), "--config-file", str(regressed)])
    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_cli_check_exits_two_on_an_unreadable_fixture(tmp_path: Path) -> None:
    junk = tmp_path / "junk.json"
    junk.write_text('{"format": "something else"}')
    assert main(["accuracy", "check", "--fixture", str(junk)]) == 2


# --- the fixture keeps only its allow-list ---------------------------------------


def _case_with_everything() -> Case:
    return Case(
        truth=Truth("IN", "Karnataka", "Bengaluru Urban", "Bengaluru"),
        consented=True,
        path="cloudflare",
        asn=AsnInfo(asn=24560, org="Bharti Airtel Ltd.", is_mobile=True, modal_share=0.2),
        tz_countries=frozenset({"IN"}),
        candidates=(
            Candidate(
                source=InferenceSource.RDNS,
                level=GeoLevel.CITY,
                country_code="IN",
                admin1="Karnataka",
                city="Bengaluru",
                lat=12.97194,
                lng=77.59369,
                raw_confidence=0.9,
                evidence={"ptr": "abts-kk-static-#.airtelbroadband.in"},
                latency_ms=41,
            ),
        ),
        network="airtel",
        connection_kind="wifi",
        vpn_used=False,
    )


def test_the_export_holds_no_identity_coordinate_or_evidence(tmp_path: Path) -> None:
    out = tmp_path / "real.json.gz"
    fixture.write(
        out, fixture.dumps([_case_with_everything()], settings_version=3, config=DEFAULT_CONFIG)
    )
    text = gzip.decompress(out.read_bytes()).decode()
    for forbidden in (
        "24560",
        "Bharti",
        "12.97",
        "77.59",
        "airtelbroadband",
        "ptr",
        "latency",
        'lat"',
        "evidence",
    ):
        assert forbidden not in text, forbidden
    case = json.loads(text)["cases"][0]
    assert set(case) == {
        "truth",
        "consented",
        "path",
        "network",
        "connection_kind",
        "vpn_used",
        "asn",
        "tz_countries",
        "candidates",
    }
    assert set(case["candidates"][0]) == {
        "source",
        "level",
        "country_code",
        "admin1",
        "admin2",
        "city",
        "raw_confidence",
    }


def test_the_export_round_trips_and_scores_the_same(tmp_path: Path) -> None:
    original = [_case_with_everything()]
    out = tmp_path / "f.json.gz"
    fixture.write(out, fixture.dumps(original, settings_version=3, config=DEFAULT_CONFIG))
    loaded = fixture.read(out)
    assert loaded.settings_version == 3
    assert loaded.config == DEFAULT_CONFIG
    before = score(
        original, DEFAULT_CONFIG, settings_version=3, inference_version="t", classifier_version="t"
    )
    after = score(
        loaded.cases,
        DEFAULT_CONFIG,
        settings_version=3,
        inference_version="t",
        classifier_version="t",
    )
    assert before.populations == after.populations
