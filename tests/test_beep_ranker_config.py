"""The shipped beep ranker is the trainer's final fit (#949, spec 2026-10-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.config import BeepDetectConfig, BeepDetection, BeepRankerConfig

REPORT = Path(__file__).resolve().parent / "fixtures" / "beep_calibration" / "ranker_report.json"


def test_the_shipped_ranker_is_the_reports_final_fit() -> None:
    """Retraining means pasting the report's final_fit into BeepRankerConfig;
    a hand edit or a stale paste fails here."""
    fit = json.loads(REPORT.read_text())["models"]["lr"]["final_fit"]
    shipped = BeepRankerConfig()
    assert shipped.model_version == fit["model_version"]
    assert list(shipped.features) == fit["features"]
    assert list(shipped.mean) == fit["mean"]
    assert list(shipped.scale) == fit["scale"]
    assert list(shipped.coef) == fit["coef"]
    assert shipped.intercept == fit["intercept"]
    assert list(shipped.head_coef) == fit["head"]["coef"]
    assert shipped.head_intercept == fit["head"]["intercept"]


def test_a_coefficient_list_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="coef"):
        BeepRankerConfig(coef=(1.0,))


def test_features_in_another_order_are_refused() -> None:
    shipped = BeepRankerConfig()
    with pytest.raises(ValueError, match="features"):
        BeepRankerConfig(features=tuple(reversed(shipped.features)))


def test_the_detector_config_carries_the_learned_ranker_by_default() -> None:
    assert BeepDetectConfig().ranker.ranker == "learned"


def test_an_old_detection_without_a_ranker_version_loads() -> None:
    old = {"time": 1.0, "peak_amplitude": 0.1, "duration_ms": 300.0}
    assert BeepDetection.model_validate(old).ranker_version is None


def test_the_shipped_head_auto_trusts_no_wrong_beep_out_of_fold() -> None:
    """The 0.97 auto-trust default rests on this (spec, "Decision after the
    report"). A retrain that lets a wrong beep past 0.97 out of fold fails
    here, not silently in the field."""
    from splitsmith.automation import AutomationSettings

    report = json.loads(REPORT.read_text())["models"]["lr"]
    threshold = AutomationSettings().beep_low_confidence_threshold
    [at_threshold] = [b for b in report["final_head_bins"] if b["threshold"] == threshold]
    assert at_threshold["wrong"] == 0
    assert at_threshold["n"] > 0
