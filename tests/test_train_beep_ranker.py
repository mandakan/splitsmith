"""The beep ranker trainer (#949, spec 2026-10-06 section 2)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from splitsmith.beep_calibration import BeepFixtureEntry
from splitsmith.config import BeepCandidate, BeepDetection, BeepFeatures

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "train_beep_ranker.py"


def _script():
    spec = importlib.util.spec_from_file_location("train_beep_ranker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _features(x: float = 0.0) -> BeepFeatures:
    return BeepFeatures(
        log_silence=x,
        tonal_ratio=x,
        duration_ms=x,
        log_peak_over_floor=x,
        peak_over_global=x,
        spectral_flatness=x,
        log_spectral_prominence=x,
    )


def _candidate(time: float, x: float = 0.0) -> BeepCandidate:
    return BeepCandidate(
        time=time, score=x, peak_amplitude=0.1, duration_ms=300.0, confidence=0.5, features=_features(x)
    )


def _entry(stem: str = "stage-shots-tallmilan-2026-stage3-s97dcec94", truth: float = 5.0, tol: float = 15.0):
    return BeepFixtureEntry(
        stem=stem, camera_kind="hand", clip_wav=f"{stem}.wav", ground_truth_in_clip=truth, tolerance_ms=tol
    )


def test_fixtures_group_by_match() -> None:
    m = _script()
    assert (
        m.match_group("stage-shots-hfo-masters-2026-stage4-s36ed6e4e-apple-iphone17pro") == "hfo-masters-2026"
    )
    assert m.match_group("stage-shots-tallmilan-2026-stage3-s97dcec94") == "tallmilan-2026"
    assert (
        m.match_group("stage-shots-vads-easter-shoot-gotta-go-fast-stage2-s36ed6e4e")
        == "vads-easter-shoot-gotta-go-fast"
    )
    assert m.match_group("beep-test") == "beep-test"


def test_a_candidate_is_positive_within_its_own_fixtures_tolerance() -> None:
    m = _script()
    detection = BeepDetection(
        time=5.010, peak_amplitude=0.1, duration_ms=300.0, candidates=[_candidate(5.010), _candidate(5.020)]
    )
    tight = m.clip_from_detection(_entry(tol=15.0), detection)
    loose = m.clip_from_detection(_entry(tol=100.0), detection)
    assert [r.positive for r in tight.rows] == [True, False]
    assert [r.positive for r in loose.rows] == [True, True]


def test_a_clip_with_no_detection_is_an_unreachable_miss() -> None:
    clip = _script().clip_from_detection(_entry(), None)
    assert clip.rows == [] and not clip.reachable


def test_rows_carry_features_and_the_heuristic_ranking() -> None:
    m = _script()
    detection = BeepDetection(
        time=1.0, peak_amplitude=0.1, duration_ms=300.0, candidates=[_candidate(1.0, 0.7)]
    )
    [row] = m.clip_from_detection(_entry(), detection).rows
    assert row.features == [0.7] * 7
    assert (row.heuristic_score, row.heuristic_confidence) == (0.7, 0.5)
    assert row.group == "tallmilan-2026"
