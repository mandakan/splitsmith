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


class _SpyModel:
    """Records the labels each fit saw; predicts the first feature as P."""

    def __init__(self, log: list) -> None:
        self.log = log
        self.stems: list[str] = []

    def fit(self, x, y, sample_weight=None):
        self.log.append((sorted(set(self.stems)), [int(v) for v in y]))
        return self

    def predict_proba(self, x):
        import numpy as np

        p = np.clip(np.asarray(x)[:, 0], 0.01, 0.99)
        return np.column_stack([1 - p, p])


def _spy_hooks(m, monkeypatch):
    ranker_fits: list = []
    head_fits: list = []
    monkeypatch.setattr(m, "_fit_hook", lambda model, train: setattr(model, "stems", [c.stem for c in train]))
    monkeypatch.setattr(m, "_make_head", lambda: _SpyModel(head_fits))
    monkeypatch.setattr(
        m, "_head_fit_hook", lambda head, train: setattr(head, "stems", [c.stem for c in train])
    )
    return ranker_fits, head_fits


def test_the_ranker_trains_on_reachable_clips_and_the_head_on_all(monkeypatch) -> None:
    """Spec 2.1 and 4: an unreachable clip (no candidate at the beep) never
    trains the ranker, but does train the confidence head, which is how
    the head learns to stay low when nothing in a clip looks like a beep."""
    m = _script()
    ranker_fits, head_fits = _spy_hooks(m, monkeypatch)
    clips = [
        _clip(m, "stage-shots-a-2026-stage1-s0", [True, False], [0.9, 0.1]),
        _clip(m, "stage-shots-a-2026-stage2-s0", [False, False], [0.8, 0.2]),
        _clip(m, "stage-shots-b-2026-stage1-s0", [True, False], [0.9, 0.1]),
        _clip(m, "stage-shots-b-2026-stage2-s0", [False, False], [0.7, 0.3]),
    ]
    probs = m.oof_probs(clips, lambda: _SpyModel(ranker_fits))
    m.oof_head_confidence(clips, probs)

    assert [stems for stems, _ in ranker_fits] == [
        ["stage-shots-b-2026-stage1-s0"],
        ["stage-shots-a-2026-stage1-s0"],
    ]
    assert [stems for stems, _ in head_fits] == [
        ["stage-shots-b-2026-stage1-s0", "stage-shots-b-2026-stage2-s0"],
        ["stage-shots-a-2026-stage1-s0", "stage-shots-a-2026-stage2-s0"],
    ]


def test_the_head_learns_whether_the_top1_is_right_not_whether_a_row_is_positive(monkeypatch) -> None:
    """A positive candidate that is not its clip's top-1 is a 0 for the head:
    the head answers "is the chosen beep right?"."""
    m = _script()
    _, head_fits = _spy_hooks(m, monkeypatch)
    loser = _clip(m, "stage-shots-a-2026-stage1-s0", [False, True], [0.9, 0.1])
    winner = _clip(m, "stage-shots-a-2026-stage2-s0", [True, False], [0.9, 0.1])
    held = _clip(m, "stage-shots-b-2026-stage1-s0", [True, False], [0.9, 0.1])
    probs = {c.stem: [r.features[0] for r in c.rows] for c in (loser, winner, held)}

    m.oof_head_confidence([loser, winner, held], probs)

    fitted_without_b = next(y for stems, y in head_fits if "stage-shots-b-2026-stage1-s0" not in stems)
    assert fitted_without_b == [0, 0, 1, 0]


def test_a_wrong_pick_at_exactly_the_auto_trust_threshold_fails_the_gate() -> None:
    m = _script()
    right = [m.ClipOutcome(stem=f"s{i}", tags=[], top1=True, topn=True, confidence=0.99) for i in range(110)]
    edge = [m.ClipOutcome(stem="edge", tags=[], top1=False, topn=True, confidence=m.AUTO_TRUST)]
    assert m.gate(right + edge).wrong_at_95 == 1


def test_rows_record_how_far_each_candidate_is_from_the_truth() -> None:
    """The report shows a wrong pick's offset, so an onset 0.2 ms outside
    tolerance reads differently from a different event."""
    m = _script()
    detection = BeepDetection(
        time=5.01519, peak_amplitude=0.1, duration_ms=300.0, candidates=[_candidate(5.01519), _candidate(2.0)]
    )
    rows = m.clip_from_detection(_entry(truth=5.0, tol=15.0), detection).rows
    assert [round(r.time_error_ms, 2) for r in rows] == [15.19, -3000.0]
    assert [r.positive for r in rows] == [False, False]


def test_a_candidate_table_shows_both_rankings_and_the_offsets() -> None:
    """Spec 2.5: a lost fixture is shown with both rankings' candidate tables."""
    m = _script()
    rows = [
        m.CandidateRow(
            stem="s",
            group="g",
            features=[0.0] * 7,
            positive=p,
            heuristic_score=h,
            heuristic_confidence=0.5,
            time_error_ms=e,
        )
        for p, h, e in ((True, 0.9, 2.0), (False, 0.4, 900.0), (False, 0.1, -3000.0))
    ]
    clip = m.Clip(stem="s", group="g", rows=rows)

    table = m.candidate_table(clip, [0.2, 0.7, 0.1])

    assert [(t["model_rank"], t["heuristic_rank"], t["time_error_ms"], t["positive"]) for t in table] == [
        (1, 2, 900.0, False),
        (2, 1, 2.0, True),
        (3, 3, -3000.0, False),
    ]
    assert table[0]["model_prob"] == 0.7 and table[1]["heuristic_score"] == 0.9
