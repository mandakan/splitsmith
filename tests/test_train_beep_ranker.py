"""The beep ranker trainer (#949, spec 2026-10-06 section 2)."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

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


def test_logits_are_clamped_and_a_lone_candidate_has_a_defined_margin() -> None:
    m = _script()
    assert m.clip_logits([0.0, 1.0]) == [-m.LOGIT_CLAMP, m.LOGIT_CLAMP]
    assert m.margins([3.0]) == [3.0 + m.LOGIT_CLAMP]
    assert m.margins([3.0, 1.0, -2.0]) == [2.0, -2.0, -5.0]
    assert all(math.isfinite(v) for v in m.margins([m.LOGIT_CLAMP]))


def _clip(m, stem: str, positives: list[bool], xs: list[float]):
    group = m.match_group(stem)
    rows = [
        m.CandidateRow(
            stem=stem,
            group=group,
            features=[x] * 7,
            positive=p,
            heuristic_score=0.0,
            heuristic_confidence=0.5,
        )
        for p, x in zip(positives, xs, strict=True)
    ]
    return m.Clip(stem=stem, group=group, rows=rows)


def test_a_separable_corpus_scores_perfectly_out_of_fold() -> None:
    """The beep always has the larger feature value; three matches."""
    from sklearn.linear_model import LogisticRegression

    m = _script()
    clips = [
        _clip(m, f"stage-shots-{match}-2026-stage{n}-s0", [False, True, False], [0.1, 0.9 + n / 100, 0.2])
        for match in ("a", "b", "c")
        for n in range(1, 5)
    ]
    probs = m.oof_probs(clips, lambda: LogisticRegression())
    assert set(probs) == {c.stem for c in clips}
    for clip in clips:
        p = probs[clip.stem]
        assert p.index(max(p)) == 1


def test_out_of_fold_never_sees_its_own_match() -> None:
    """A model that memorises its training stems would score a held-out
    match at chance; this one records which stems it was fitted on."""
    m = _script()
    fitted_on: list[set[str]] = []

    class Spy:
        def fit(self, x, y, sample_weight=None):
            fitted_on.append(set(self._stems))
            return self

        def predict_proba(self, x):
            import numpy as np

            return np.tile([0.5, 0.5], (len(x), 1))

    clips = [_clip(m, f"stage-shots-{g}-2026-stage1-s0", [True, False], [1.0, 0.0]) for g in ("a", "b")]

    def make():
        spy = Spy()
        spy._stems = []
        return spy

    m._fit_hook = lambda model, train: setattr(model, "_stems", [c.stem for c in train])
    m.oof_probs(clips, make)
    assert fitted_on == [{"stage-shots-b-2026-stage1-s0"}, {"stage-shots-a-2026-stage1-s0"}]


def test_one_confident_wrong_fixture_fails_the_gate() -> None:
    m = _script()
    right = [m.ClipOutcome(stem=f"s{i}", tags=[], top1=True, topn=True, confidence=0.99) for i in range(110)]
    wrong = [m.ClipOutcome(stem=f"w{i}", tags=[], top1=False, topn=False, confidence=0.4) for i in range(17)]
    assert m.gate(right + wrong).passed
    confident_wrong = [m.ClipOutcome(stem="x", tags=[], top1=False, topn=True, confidence=0.96)]
    result = m.gate(right[:-1] + wrong + confident_wrong)
    assert (result.wrong_at_95, result.passed) == (1, False)


def test_the_gate_floors_are_the_specs() -> None:
    m = _script()
    assert (m.TOP1_FLOOR, m.TOPN_FLOOR) == (78, 105)
    hits = [
        m.ClipOutcome(stem=f"s{i}", tags=[], top1=i < 77, topn=i < 110, confidence=0.5) for i in range(127)
    ]
    assert not m.gate(hits).passed
    hits = [
        m.ClipOutcome(stem=f"s{i}", tags=[], top1=i < 78, topn=i < 104, confidence=0.5) for i in range(127)
    ]
    assert not m.gate(hits).passed


def test_an_unreachable_clip_counts_as_a_miss() -> None:
    m = _script()
    clips = [m.Clip(stem="stage-shots-a-2026-stage1-s0", group="a", rows=[])]
    [outcome] = m.outcomes(clips, {}, {}, {"stage-shots-a-2026-stage1-s0": ["handheld"]})
    assert (outcome.top1, outcome.topn, outcome.confidence) == (False, False, 0.0)


def test_top1_counts_when_either_of_two_positive_candidates_wins() -> None:
    m = _script()
    clip = _clip(m, "stage-shots-a-2026-stage1-s0", [True, True, False], [0.0, 0.0, 0.0])
    [outcome] = m.outcomes(
        [clip], {clip.stem: [0.2, 0.7, 0.1]}, {clip.stem: [0.1, 0.8, 0.0]}, {clip.stem: []}
    )
    assert outcome.top1 and outcome.confidence == pytest.approx(0.8)
