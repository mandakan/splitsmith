"""Whole-stage decoding (``ensemble.decoder``): the best set of candidates
for a stage, from recalibrated probabilities, a split prior and the timer
stop. Inputs are hand-built candidate scores, not audio."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from splitsmith.ensemble.calibration import EnsembleCalibration
from splitsmith.ensemble.decoder import PriorTable, StageDecoder, decode_stage


def _model(**over) -> StageDecoder:
    # Split prior: favours 0.2-0.6 s (log10 -0.7..-0.2), neutral elsewhere.
    grid = np.arange(-1.2, 1.01, 0.05)
    split = np.where((grid > -0.75) & (grid < -0.2), 1.0, 0.0)
    # End prior: peaks when the last shot is at the timer stop.
    ends = np.arange(-1.0, 0.3, 0.01)
    end = -((ends / 0.03) ** 2) / 2
    kw = {
        "coef": [1.0, 0, 0, 0, 0, 0],
        "intercept": 0.0,
        "bias": 0.0,
        "w_split": 1.0,
        "w_end": 0.0,
        "split_prior": PriorTable(start=-1.2, step=0.05, values=split.tolist()),
        "end_prior": PriorTable(start=-1.0, step=0.01, values=end.tolist()),
    }
    kw.update(over)
    return StageDecoder(**kw)


def _decode(model, times, probs, *, stage_time=5.0, duration=10.0):
    return decode_stage(
        model, np.array(times), np.array(probs), beep_time=1.0, stage_time=stage_time, clip_duration=duration
    ).tolist()


def test_keeps_likely_shots_and_drops_unlikely_ones() -> None:
    keep = _decode(_model(), [2.0, 2.5, 3.0, 3.6], [0.95, 0.05, 0.9, 0.97])
    assert keep == [True, False, True, True]


def test_never_keeps_two_shots_closer_than_the_minimum_split() -> None:
    """An echo 40 ms after a shot: only the likelier of the two is a shot."""
    keep = _decode(_model(), [2.0, 2.04, 2.6], [0.9, 0.8, 0.9])
    assert keep == [True, False, True]


def test_the_split_prior_keeps_a_doubtful_shot_at_a_typical_split() -> None:
    """p 0.4 alone is not kept; between two shots at 0.3 s splits, the split
    prior carries it. Without the prior it is dropped."""
    times, probs = [2.0, 2.3, 2.6], [0.95, 0.4, 0.95]
    assert _decode(_model(w_split=1.0), times, probs) == [True, True, True]
    assert _decode(_model(w_split=0.0), times, probs) == [True, False, True]


def test_nothing_after_the_timer_stopped_when_the_clip_runs_past_it() -> None:
    """Beep 1.0 + stage 5.0: the timer stopped at 6.0. A likely candidate at
    6.5 is an echo or another bay; in a clip that ends at 6.1 it is not
    there to judge, and nothing is blocked."""
    times, probs = [2.0, 5.9, 6.5], [0.9, 0.9, 0.95]
    assert _decode(_model(), times, probs, duration=10.0) == [True, True, False]
    assert _decode(_model(), times[:2], probs[:2], duration=6.1) == [True, True]


def test_nothing_before_the_beep_and_a_short_draw() -> None:
    keep = _decode(_model(), [0.5, 1.1, 2.0], [0.99, 0.99, 0.9])
    assert keep == [False, False, True]


def test_the_end_prior_pulls_the_last_shot_to_the_timer_stop() -> None:
    """Two rival last shots 40 ms apart, the earlier a little likelier: the
    one on the timer stop (6.0) wins with the end prior on."""
    times, probs = [2.0, 5.96, 6.0], [0.95, 0.75, 0.7]
    assert _decode(_model(w_end=0.0), times, probs) == [True, True, False]
    assert _decode(_model(w_end=1.0), times, probs) == [True, False, True]


def _count_model(**over) -> StageDecoder:
    """A count prior peaked at k == expected (sigma 1 shot)."""
    ks = np.arange(-20.0, 20.5, 0.5)
    kw = {"w_count": 1.0, "count_prior": PriorTable(start=-20.0, step=0.5, values=(-(ks**2) / 2).tolist())}
    return _model(**{**kw, **over})


def test_a_known_round_count_keeps_low_scoring_shots_of_a_weak_camera() -> None:
    """Six real shots scored 0.3 by a camera the model is unsure of: alone
    they are dropped; with six rounds expected they are kept."""
    times = [2.0, 2.4, 2.8, 3.2, 3.6, 4.0]
    probs = [0.3] * 6
    m = _count_model()
    assert _decode(m, times, probs) == [False] * 6
    keep = decode_stage(
        m,
        np.array(times),
        np.array(probs),
        beep_time=1.0,
        stage_time=5.0,
        clip_duration=10.0,
        expected_rounds=6,
    )
    assert keep.tolist() == [True] * 6


def test_a_known_round_count_drops_the_least_likely_extras() -> None:
    """Four shots expected, six likely candidates: the count prior drops the
    two least likely rather than keep them all."""
    times = [2.0, 2.4, 2.8, 3.2, 3.6, 4.0]
    probs = [0.95, 0.95, 0.7, 0.95, 0.95, 0.7]
    keep = decode_stage(
        _count_model(w_count=3.0, w_split=0.0),
        np.array(times),
        np.array(probs),
        beep_time=1.0,
        stage_time=5.0,
        clip_duration=10.0,
        expected_rounds=4,
    )
    assert keep.tolist() == [True, True, False, True, True, False]


def test_without_a_round_count_the_count_prior_is_not_used() -> None:
    times, probs = [2.0, 2.4, 2.8], [0.95, 0.3, 0.95]
    assert _decode(_count_model(), times, probs) == _decode(_model(), times, probs)


def test_candidates_in_any_order_and_an_empty_stage() -> None:
    assert _decode(_model(), [3.0, 2.0], [0.9, 0.9]) == [True, True]
    assert _decode(_model(), [], []) == []
    assert _decode(_model(), [2.0], [0.01]) == [False]


def test_the_calibration_carries_a_decoder_and_loads_without_one() -> None:
    """A set without decoders loads and decodes with none; one with a
    decoder per class round-trips it through the JSON."""
    from tests._ensemble_stubs import stub_calibration

    model = _model()
    plain = stub_calibration()
    assert plain.model_set("clap").decoder_for("headcam", "headcam") is None
    payload = plain.model_dump()
    payload["model_sets"]["clap"]["decoders_by_camera_class"] = {"headcam": model.model_dump()}
    cal = EnsembleCalibration.model_validate(json.loads(json.dumps(payload)))
    decoder = cal.model_set("clap").decoder_for("headcam", "headcam")
    assert decoder is not None
    assert math.isclose(decoder.split_prior(np.array([-0.5]))[0], 1.0)


def _fit_module():
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    import sys

    if scripts not in sys.path:
        sys.path.append(scripts)
    import fit_stage_decoder

    return fit_stage_decoder


def _synthetic_corpus(seed: int = 0):
    """Two matches, eight stages each: shots every 0.35 s after a 1.2 s
    draw, an echo 40 ms after each (scored a little lower), and noise. The
    last shot is the timer stop."""
    rng = np.random.default_rng(seed)
    rows, stages = [], {}
    for m in ("alpha", "bravo"):
        for k in range(8):
            fix = f"{m}-{k}"
            shots = [2.2 + 0.35 * i for i in range(10)]
            stages[fix] = {"truth": shots, "beep": 1.0, "stage_time": shots[-1] - 1.0, "duration": 30.0}
            cands = [(t, 1, rng.uniform(0.6, 0.95)) for t in shots]
            cands += [(t + 0.04, 0, rng.uniform(0.4, 0.7)) for t in shots]
            cands += [(float(t), 0, rng.uniform(0.0, 0.3)) for t in rng.uniform(1.5, 8.0, 10)]
            for t, y, s in cands:
                rows.append(
                    {
                        "fixture": fix,
                        "match": m,
                        "t_shot": t,
                        "label": y,
                        "score_c": s,
                        "clap_diff": 0.0,
                        "gunshot_prob": s,
                        "confidence": 0.5,
                        "peak_amp": 0.3,
                        "vote_b": 1,
                    }
                )
    return rows, stages


def test_the_fit_learns_to_keep_shots_and_drop_their_echoes() -> None:
    fsd = _fit_module()
    rows, stages = _synthetic_corpus()
    model = fsd.fit_stage_decoder(rows, stages, tolerance_ms=15.0, log=lambda _m: None)
    assert model is not None
    oof = model.fit_report["out_of_fold"]
    assert oof["f1"] > 0.95, oof
    # Decoding a fresh stage with the shipped recalibration recovers its shots.
    test_rows, test_stages = _synthetic_corpus(seed=1)
    fix = "alpha-0"
    mine = [r for r in test_rows if r["fixture"] == fix]
    probs = (
        fsd.decoder_features(
            np.array([r["score_c"] for r in mine]),
            np.zeros(len(mine)),
            np.array([r["gunshot_prob"] for r in mine]),
            np.full(len(mine), 0.5),
            np.full(len(mine), 0.3),
            np.ones(len(mine)),
        )
        @ np.array(model.coef)
        + model.intercept
    )
    keep = decode_stage(
        model,
        np.array([r["t_shot"] for r in mine]),
        1 / (1 + np.exp(-probs)),
        beep_time=1.0,
        stage_time=test_stages[fix]["stage_time"],
        clip_duration=30.0,
    )
    kept = sorted(r["t_shot"] for r, k in zip(mine, keep, strict=True) if k)
    assert fsd.match_count(kept, test_stages[fix]["truth"], 0.015) == (10, 0, 0)


def test_one_match_cannot_be_held_out_against_itself() -> None:
    fsd = _fit_module()
    rows, stages = _synthetic_corpus()
    alpha = [r for r in rows if r["match"] == "alpha"]
    assert fsd.fit_stage_decoder(alpha, stages, tolerance_ms=15.0, log=lambda _m: None) is None


def test_the_fast_floor_stops_penalising_splits_faster_than_the_corpus() -> None:
    """Corpus splits are all 0.35 s; a 0.15 s split is far below the prior's
    mode. With a 0.2 s floor it scores what a 0.2 s split does."""
    fsd = _fit_module()
    _, stages = _synthetic_corpus()
    plain, _ = fsd.fit_priors(list(stages.values()))
    floored, _ = fsd.fit_priors(list(stages.values()), fast_floor_s=0.2)
    at = lambda table, s: float(table(np.array([math.log10(s)]))[0])  # noqa: E731
    assert at(plain, 0.15) < at(plain, 0.2)
    assert math.isclose(at(floored, 0.15), at(plain, 0.2), abs_tol=1e-4)
    assert math.isclose(at(floored, 0.35), at(plain, 0.35), abs_tol=1e-6)


def _stub_detection(monkeypatch, times, gunshot):
    """The ensemble on the stub runtime from test_ensemble, with the given
    candidate times and a per-candidate gunshot probability (the stub GBDT
    and CLAP give every candidate the same scores)."""
    from splitsmith.config import Shot
    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features
    from tests._ensemble_stubs import with_set_update
    from tests.test_ensemble import CLAP_PROMPTS, _build_stub_runtime

    shots = [
        Shot(
            shot_number=i + 1,
            time_absolute=t,
            time_from_beep=t - 1.0,
            split=0.3,
            peak_amplitude=0.4,
            confidence=0.6,
            notes="",
        )
        for i, t in enumerate(times)
    ]
    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, t, runtime: np.full((len(t), len(CLAP_PROMPTS)), 0.5, dtype=np.float32),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, t, runtime: np.array(gunshot, dtype=np.float32),
    )
    runtime = _build_stub_runtime()
    # A decoder that reads only the gunshot probability (logit = 10 * (g - 0.5)).
    model = _model(coef=[0, 0, 10.0, 0, 0, 0], intercept=-5.0)
    runtime.calibration = with_set_update(
        runtime.calibration, "clap", decoders_by_camera_class={"headcam": model, "handheld": model}
    )
    return runtime


def test_the_runtime_decodes_by_default_and_can_be_turned_off(monkeypatch) -> None:
    """On by default: the decoder decides (the consensus would keep all four
    stub candidates) and every candidate reports its recalibrated
    probability. ``stage_decoder=False`` or SPLITSMITH_STAGE_DECODER=0 keeps
    the consensus; the env var wins over the config both ways."""
    from splitsmith.ensemble.api import EnsembleConfig, detect_shots_ensemble

    runtime = _stub_detection(monkeypatch, [2.0, 2.3, 2.6, 2.9], [0.9, 0.1, 0.9, 0.9])
    audio = np.zeros(48_000 * 10, dtype=np.float32)
    decoded, consensus = [True, False, True, True], [True, True, True, True]

    def kept(**kw):
        res = detect_shots_ensemble(audio, 48_000, beep_time=1.0, stage_time=2.0, runtime=runtime, **kw)
        return res, [c.kept for c in res.candidates]

    monkeypatch.delenv("SPLITSMITH_STAGE_DECODER", raising=False)
    on, keep = kept()
    assert keep == decoded
    assert on.candidates[1].decoder_prob < 0.05 < on.candidates[0].decoder_prob

    off, keep = kept(ensemble_config=EnsembleConfig(stage_decoder=False))
    assert keep == consensus
    assert all(c.decoder_prob is None for c in off.candidates)

    monkeypatch.setenv("SPLITSMITH_STAGE_DECODER", "0")
    assert kept()[1] == consensus
    monkeypatch.setenv("SPLITSMITH_STAGE_DECODER", "1")
    assert kept(ensemble_config=EnsembleConfig(stage_decoder=False))[1] == decoded


def test_a_calibration_without_a_decoder_keeps_the_consensus(monkeypatch) -> None:
    from splitsmith.ensemble.api import EnsembleConfig, detect_shots_ensemble
    from tests._ensemble_stubs import with_set_update

    runtime = _stub_detection(monkeypatch, [2.0, 2.3], [0.9, 0.1])
    runtime.calibration = with_set_update(runtime.calibration, "clap", decoders_by_camera_class={})
    res = detect_shots_ensemble(
        np.zeros(48_000 * 10, dtype=np.float32),
        48_000,
        beep_time=1.0,
        stage_time=2.0,
        runtime=runtime,
        ensemble_config=EnsembleConfig(stage_decoder=True),
    )
    assert [c.kept for c in res.candidates] == [True, True]
