"""detect_beep ranks with the learned ranker (#949, spec 2026-10-06 sections 3-4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from splitsmith.beep_calibration import load_manifest
from splitsmith.beep_detect import _candidate_runs, _learned_scores, detect_beep, load_audio
from splitsmith.config import BeepDetectConfig, BeepRankerConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST = load_manifest(FIXTURES / "beep_calibration" / "manifest.yaml")
HEURISTIC = BeepDetectConfig(ranker=BeepRankerConfig(ranker="heuristic"))

# The heuristic gets this one wrong; the out-of-fold LR gets it right
# (ranker_report.json, models.lr.fixed, one of 42).
FIXED = "stage-shots-blacksmith-2026-stage6-s97dcec94-apple-iphone17pro"


def _entry(stem: str):
    return next(e for e in MANIFEST.fixtures if e.stem == stem)


def _single_run_fixture():
    sr = 48_000
    audio = np.random.default_rng(0).normal(0.0, 0.002, 2 * sr).astype(np.float32)
    t = np.arange(int(0.4 * sr)) / sr
    audio[sr : sr + t.size] += (0.5 * np.sin(2 * np.pi * 3000.0 * t)).astype(np.float32)
    runs = _candidate_runs(audio, sr, BeepDetectConfig())
    assert len(runs) == 1
    return runs[0]


def test_the_learned_ranker_picks_the_beep_the_heuristic_missed() -> None:
    e = _entry(FIXED)
    audio, sr = load_audio(FIXTURES / e.clip_wav)
    learned = detect_beep(audio, sr, BeepDetectConfig())
    heuristic = detect_beep(audio, sr, HEURISTIC)
    tol = e.tolerance_ms / 1000.0
    assert abs(learned.time - e.ground_truth_in_clip) <= tol
    assert abs(heuristic.time - e.ground_truth_in_clip) > tol
    assert learned.ranker_version == BeepRankerConfig().model_version
    assert heuristic.ranker_version == "heuristic"


def test_candidates_are_ordered_by_probability_and_confidence_is_the_heads() -> None:
    e = MANIFEST.fixtures[1]
    audio, sr = load_audio(FIXTURES / e.clip_wav)
    d = detect_beep(audio, sr, BeepDetectConfig(top_n_candidates=1000))
    scores = [c.score for c in d.candidates]
    assert len(scores) > 1
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= c.confidence <= 1.0 for c in d.candidates)
    assert d.confidence == d.candidates[0].confidence
    assert d.candidates[0].confidence >= max(c.confidence for c in d.candidates[1:])


def test_a_lone_candidate_gets_a_finite_confidence() -> None:
    [(prob, conf)] = _learned_scores([_single_run_fixture()], BeepRankerConfig())
    assert 0.0 < prob < 1.0 and 0.0 <= conf <= 1.0


def test_equal_probabilities_score_equally() -> None:
    run = _single_run_fixture()
    scores = _learned_scores([run, run], BeepRankerConfig())
    assert scores[0] == scores[1]


def test_the_arithmetic_is_the_trainers() -> None:
    """A lone run's margin is its logit plus the clamp (the trainer's rule),
    and the head reads (logit, margin) in that order."""
    import math

    ranker = BeepRankerConfig()
    run = _single_run_fixture()
    x = [getattr(run.features, n) for n in ranker.features]
    z = ranker.intercept + sum(
        c * (v - m) / s for c, v, m, s in zip(ranker.coef, x, ranker.mean, ranker.scale, strict=True)
    )
    z = max(-10.0, min(10.0, z))
    a, b = ranker.head_coef
    expected_conf = 1.0 / (1.0 + math.exp(-(a * z + b * (z + 10.0) + ranker.head_intercept)))
    [(prob, conf)] = _learned_scores([run], ranker)
    assert prob == 1.0 / (1.0 + math.exp(-z))
    assert abs(conf - expected_conf) < 1e-12
