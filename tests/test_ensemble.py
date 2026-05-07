"""Unit tests for the 4-voter ensemble logic.

The voters are pure functions over per-candidate signals -- testable
without loading CLAP/PANN/GBDT. The end-to-end ``detect_shots_ensemble``
test uses a stubbed ``EnsembleRuntime`` to exercise the wiring while
side-stepping the heavy model paths.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from splitsmith.config import Shot
from splitsmith.ensemble import (
    EnsembleConfig,
    detect_shots_ensemble,
)
from splitsmith.ensemble.api import EnsembleRuntime
from splitsmith.ensemble.calibration import EnsembleCalibration
from splitsmith.ensemble.features import (
    CLAP_PROMPTS,
    CLAP_PROMPTS_SHOT,
    HAND_FEATURE_DIM,
    VOTER_C_FEATURE_DIM,
    clap_diff_from_similarities,
    voter_c_feature_matrix,
)
from splitsmith.ensemble.voters import (
    apriori_boost,
    consensus_keep,
    vote_a,
    vote_b,
    vote_c_adaptive,
    vote_c_global,
    vote_d,
    vote_e,
)


def test_vote_a_floor_threshold() -> None:
    confidences = np.array([0.0, 0.05, 0.1, 0.5])
    out = vote_a(confidences, voter_a_floor=0.05)
    assert out.tolist() == [0, 1, 1, 1]


def test_vote_b_clap_diff_threshold() -> None:
    diffs = np.array([-0.1, 0.0, 0.05, 0.5])
    out = vote_b(diffs, voter_b_threshold=0.05)
    assert out.tolist() == [0, 0, 1, 1]


def test_vote_c_global_threshold() -> None:
    probs = np.array([0.1, 0.2, 0.3, 0.9])
    out = vote_c_global(probs, voter_c_threshold=0.25)
    assert out.tolist() == [0, 0, 1, 1]


def test_vote_c_adaptive_top_k_plus_slack() -> None:
    """K=5 with default slack_min=3 -> keep top-8 candidates by GBDT prob.

    Default ``slack_frac=0.25`` (issue #103) → ``max(3, 5*0.25=1.25)=3``,
    so K + slack = 8. Wider K would tip into the fractional regime.
    """
    probs = np.linspace(0.0, 1.0, 20)
    out = vote_c_adaptive(probs, expected_rounds=5)
    assert out.sum() == 8
    # The top-8 by prob are indices 12..19.
    assert np.all(out[-8:] == 1)
    assert np.all(out[:-8] == 0)


def test_vote_c_adaptive_confidence_override_recovers_high_scoring_outsiders() -> None:
    """A candidate with ``score_c >= confidence_override`` passes voter C
    even when ranked outside top-(K+slack).

    Without the override, real shots that score 0.8-0.9 get silenced by
    a too-tight K cap (issue #103 follow-up: two rank-cut FNs on
    tallmilan-2026-stage6).
    """
    # 30 candidates: 25 score 0.50, 5 score 0.05 then 0.10 then 0.90.
    # K=10 -> target=13. Top-13 by score are 13 of the 0.50s.
    # Index 28 (the 0.90) is also rank-1, so it's already in top-13.
    # To exercise the override we need an outlier *outside* top-13.
    # Simulate by stacking 14 high scorers (0.50) and inserting one
    # 0.90 at index 14 -- argsort -> the 0.90 lands at rank 1 (argsort
    # is stable and -0.90 < -0.50). Wait: argsort(-x) sorts ascending
    # in -x = descending in x, so the 0.90 is rank 1. Always in top-13.
    # The override only matters when the synthesis can put the high
    # scorer past rank 13 -- which can't happen since rank goes by
    # score, and 0.90 > 0.50.
    #
    # The actual semantics: override forces high-scorers to pass even
    # when target < N. Verify by setting target > N (everyone passes
    # already, override is no-op) and target = K (small) where the
    # override flips additional candidates above the threshold. Use
    # K=2, slack_min=0 to get target=2, then check the 0.90 at index
    # 14 (rank 1) is in top-2 regardless.
    probs = np.array([0.50] * 14 + [0.90])
    out_with_override = vote_c_adaptive(
        probs, expected_rounds=2, slack_min=0, slack_frac=0.0, confidence_override=0.75
    )
    out_no_override = vote_c_adaptive(
        probs, expected_rounds=2, slack_min=0, slack_frac=0.0, confidence_override=None
    )
    # Without override: top-2 keeps 2 of the 0.50s (or the 0.90 + one 0.50).
    # Either way exactly 2 are kept.
    assert int(out_no_override.sum()) == 2
    # With override at 0.75: the 0.90 candidate is forced in. The top-2
    # by rank also keeps 2 candidates (the 0.90 + one 0.50, given
    # stable argsort), so the override doesn't add anything new here.
    # The cleaner functional check: a sub-K confidence_override floor
    # below all scores keeps everyone.
    out_zero = vote_c_adaptive(
        probs, expected_rounds=2, slack_min=0, slack_frac=0.0, confidence_override=0.0
    )
    assert int(out_zero.sum()) == probs.size
    # And confidence_override above the max keeps only top-(K+slack).
    out_high = vote_c_adaptive(
        probs, expected_rounds=2, slack_min=0, slack_frac=0.0, confidence_override=2.0
    )
    assert int(out_high.sum()) == 2
    # The two outputs are bitwise consistent in the always-in-top-K case.
    assert np.array_equal(out_with_override, out_no_override) or int(
        out_with_override.sum()
    ) >= int(out_no_override.sum())


def test_vote_c_adaptive_zero_rounds_returns_all_zero() -> None:
    probs = np.array([0.9, 0.5, 0.1])
    out = vote_c_adaptive(probs, expected_rounds=0)
    assert out.tolist() == [0, 0, 0]


def test_vote_c_adaptive_target_exceeds_universe_keeps_everything() -> None:
    probs = np.array([0.1, 0.5])
    out = vote_c_adaptive(probs, expected_rounds=10)
    assert out.tolist() == [1, 1]


def test_vote_d_pann_threshold() -> None:
    probs = np.array([0.001, 0.01, 0.1])
    out = vote_d(probs, voter_d_threshold=0.01)
    assert out.tolist() == [0, 1, 1]


def test_vote_e_visual_probe_threshold() -> None:
    probe_score = np.array([0.05, 0.30, 0.40, 0.95])
    out = vote_e(probe_score, voter_e_threshold=0.30)
    assert out.tolist() == [0, 1, 1, 1]


def test_vote_e_handles_empty_input() -> None:
    out = vote_e(np.zeros(0, dtype=np.float32), voter_e_threshold=0.5)
    assert out.shape == (0,)
    assert out.dtype == np.int64


def test_apriori_boost_lifts_top_n_by_confidence() -> None:
    confidences = np.array([0.1, 0.5, 0.9, 0.3])
    boost = apriori_boost(confidences, expected_rounds=2, boost=1.0)
    # Top-2 by confidence are indices 2 and 1.
    assert boost.tolist() == [0.0, 1.0, 1.0, 0.0]


def test_apriori_boost_disabled_when_no_expected_rounds() -> None:
    confidences = np.array([0.9, 0.1])
    out = apriori_boost(confidences, expected_rounds=None, boost=1.0)
    assert out.tolist() == [0.0, 0.0]


def test_consensus_keep_combines_votes_with_boost() -> None:
    vote_total = np.array([2, 3, 1, 4])
    boost = np.array([1.0, 0.0, 0.0, 0.0])
    out = consensus_keep(vote_total, boost, threshold=3)
    # idx 0: 2+1=3 -> keep ; idx 1: 3 -> keep; idx 2: 1 -> drop; idx 3: 4 -> keep
    assert out.tolist() == [True, True, False, True]


def test_consensus_keep_c_veto_drops_no_c_candidates() -> None:
    """Issue #103: with ``c_required=True``, a candidate that meets the
    consensus threshold via A+B+D is still dropped if voter C said no."""
    vote_total = np.array([3, 3, 4])
    boost = np.array([0.0, 0.0, 0.0])
    vote_c = np.array([0, 1, 0])
    out = consensus_keep(vote_total, boost, threshold=3, vote_c=vote_c, c_required=True)
    # idx 0: total=3 but C=0 -> drop
    # idx 1: total=3, C=1     -> keep
    # idx 2: total=4 but C=0  -> drop
    assert out.tolist() == [False, True, False]


def test_consensus_keep_c_required_without_vote_c_raises() -> None:
    vote_total = np.array([3])
    boost = np.array([0.0])
    with pytest.raises(ValueError, match="c_required=True"):
        consensus_keep(vote_total, boost, threshold=3, c_required=True)


def test_clap_diff_subtracts_not_shot_mean_from_shot_mean() -> None:
    n_shot = len(CLAP_PROMPTS_SHOT)
    n_not = len(CLAP_PROMPTS) - n_shot
    sims = np.zeros((1, len(CLAP_PROMPTS)), dtype=np.float32)
    sims[0, :n_shot] = 0.5
    sims[0, n_shot:] = 0.1
    diff = clap_diff_from_similarities(sims)
    assert diff.shape == (1,)
    assert diff[0] == pytest.approx(0.4, abs=1e-5)
    assert n_not > 0


def test_clap_diff_handles_empty_input() -> None:
    out = clap_diff_from_similarities(np.zeros((0, len(CLAP_PROMPTS))))
    assert out.shape == (0,)


def test_voter_c_feature_matrix_dimensions() -> None:
    n = 4
    hand = np.zeros((n, HAND_FEATURE_DIM))
    sims = np.zeros((n, len(CLAP_PROMPTS)))
    diff = np.zeros(n)
    x = voter_c_feature_matrix(hand, sims, diff)
    assert x.shape == (n, VOTER_C_FEATURE_DIM)


def test_voter_c_feature_matrix_empty_returns_zero_rows() -> None:
    hand = np.zeros((0, HAND_FEATURE_DIM))
    sims = np.zeros((0, len(CLAP_PROMPTS)))
    diff = np.zeros(0)
    x = voter_c_feature_matrix(hand, sims, diff)
    assert x.shape == (0, VOTER_C_FEATURE_DIM)


# ---------------------------------------------------------------------------
# End-to-end with a stubbed runtime
# ---------------------------------------------------------------------------


@dataclass
class _StubClapRuntime:
    """Mimics ``ClapRuntime`` for type-shape; never used by the stubs below."""


@dataclass
class _StubPannRuntime:
    """Mimics ``PannRuntime`` for type-shape; never used by the stubs below."""


class _StubGBDT:
    """``predict_proba`` always returns 0.95 for class 1, 0.05 for class 0."""

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        out = np.zeros((x.shape[0], 2), dtype=np.float64)
        out[:, 0] = 0.05
        out[:, 1] = 0.95
        return out


def _build_stub_runtime() -> EnsembleRuntime:
    cal = EnsembleCalibration(
        voter_a_floor=0.05,
        voter_b_threshold=0.0,
        voter_c_threshold=0.5,
        voter_d_threshold=0.0,
        voter_c_target_recall=0.95,
        tolerance_ms=75.0,
        clap_prompts_shot=list(CLAP_PROMPTS_SHOT),
        clap_prompts=list(CLAP_PROMPTS),
        calibration_fixtures=["stub"],
        n_calibration_candidates=10,
        n_calibration_positives=5,
        voter_c_feature_dim=VOTER_C_FEATURE_DIM,
        built_at="1970-01-01T00:00:00Z",
    )
    return EnsembleRuntime(
        calibration=cal,
        voter_c_model=_StubGBDT(),
        clap=_StubClapRuntime(),  # type: ignore[arg-type]
        pann=_StubPannRuntime(),  # type: ignore[arg-type]
        expected_prompts=tuple(CLAP_PROMPTS),
    )


def test_detect_shots_ensemble_full_universe_with_stubs(monkeypatch) -> None:
    """All four voters pass on every candidate with the stub runtime, so
    every candidate ends up kept (vote_total=4, consensus=3)."""
    runtime = _build_stub_runtime()
    fake_shots = [
        Shot(
            shot_number=i + 1,
            time_absolute=5.0 + i * 0.3,
            time_from_beep=i * 0.3,
            split=0.3 if i > 0 else 5.0,
            peak_amplitude=0.4,
            confidence=0.6,
            notes="",
        )
        for i in range(4)
    ]
    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features

    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: fake_shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, times, runtime: np.full(
            (len(times), len(CLAP_PROMPTS)), 0.5, dtype=np.float32
        ),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, times, runtime: np.full(len(times), 0.9, dtype=np.float32),
    )

    audio = np.zeros(48_000 * 20, dtype=np.float32)
    result = detect_shots_ensemble(
        audio,
        48_000,
        beep_time=5.0,
        stage_time=10.0,
        runtime=runtime,
    )
    assert len(result.candidates) == 4
    assert all(c.kept for c in result.candidates)
    assert all(c.vote_total == 4 for c in result.candidates)
    assert result.consensus == 3


def test_detect_shots_ensemble_apriori_boost_lifts_top_k(monkeypatch) -> None:
    """When the GBDT and CLAP/PANN signals all reject the universe but
    expected_rounds=2 is provided, the apriori boost + adaptive voter C
    keep the top-2 candidates by detector confidence."""
    runtime = _build_stub_runtime()
    # Push voter B and D thresholds high so no candidate clears them.
    runtime.calibration = runtime.calibration.model_copy(
        update={"voter_b_threshold": 99.0, "voter_d_threshold": 99.0}
    )

    confidences = [0.9, 0.4, 0.7, 0.3]
    fake_shots = [
        Shot(
            shot_number=i + 1,
            time_absolute=5.0 + i * 0.3,
            time_from_beep=i * 0.3,
            split=0.3 if i > 0 else 5.0,
            peak_amplitude=0.4,
            confidence=conf,
            notes="",
        )
        for i, conf in enumerate(confidences)
    ]

    class _RejectingGBDT:
        def predict_proba(self, x: np.ndarray) -> np.ndarray:
            out = np.zeros((x.shape[0], 2))
            out[:, 0] = 0.99
            out[:, 1] = 0.01
            return out

    runtime.voter_c_model = _RejectingGBDT()

    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features

    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: fake_shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, times, runtime: np.zeros(
            (len(times), len(CLAP_PROMPTS)), dtype=np.float32
        ),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, times, runtime: np.zeros(len(times), dtype=np.float32),
    )

    audio = np.zeros(48_000 * 20, dtype=np.float32)
    result = detect_shots_ensemble(
        audio,
        48_000,
        beep_time=5.0,
        stage_time=10.0,
        runtime=runtime,
        expected_rounds=2,
        ensemble_config=EnsembleConfig(consensus=3, apriori_boost=3.0),
    )
    # With the strong boost (3.0) on top-K=2 + adaptive voter C also
    # keeping top-(2+3)=5, the top-2 by confidence (idx 0 and 2) clear
    # consensus while the bottom 2 do not.
    kept_idx = [c.candidate_number - 1 for c in result.candidates if c.kept]
    assert kept_idx == [0, 2]


# ---------------------------------------------------------------------------
# Voter E (issue #183) wiring
# ---------------------------------------------------------------------------


class _StubVisualRuntime:
    """Stand-in for ``VisualRuntime`` -- exposes ``probe`` for scoring."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self.frame_cache_dir = None  # type: ignore[assignment]

    @property
    def probe(self):  # noqa: D401 -- minimal stub
        scores = self._scores

        class _P:
            def predict_proba(self, x: np.ndarray) -> np.ndarray:
                out = np.zeros((x.shape[0], 2), dtype=np.float64)
                vals = np.array(scores[: x.shape[0]], dtype=np.float64)
                out[:, 1] = vals
                out[:, 0] = 1.0 - vals
                return out

        return _P()


def test_detect_shots_ensemble_skips_voter_e_when_disabled(monkeypatch) -> None:
    """Default config has ``enable_voter_e=False``; Voter E must not run
    even if a visual runtime is attached. vote_e=0 / signal=0 for all."""
    runtime = _build_stub_runtime()
    runtime.visual = _StubVisualRuntime([0.99] * 4)  # type: ignore[assignment]

    fake_shots = [
        Shot(
            shot_number=i + 1,
            time_absolute=5.0 + i * 0.3,
            time_from_beep=i * 0.3,
            split=0.3 if i > 0 else 5.0,
            peak_amplitude=0.4,
            confidence=0.6,
            notes="",
        )
        for i in range(4)
    ]
    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features

    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: fake_shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, times, runtime: np.full(
            (len(times), len(CLAP_PROMPTS)), 0.5, dtype=np.float32
        ),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, times, runtime: np.full(len(times), 0.9, dtype=np.float32),
    )

    audio = np.zeros(48_000 * 20, dtype=np.float32)
    result = detect_shots_ensemble(
        audio,
        48_000,
        beep_time=5.0,
        stage_time=10.0,
        runtime=runtime,
    )
    assert all(c.vote_e == 0 for c in result.candidates)
    assert all(c.voter_e_signal == 0.0 for c in result.candidates)


def test_detect_shots_ensemble_voter_e_e_required_drops_low_score(
    monkeypatch, tmp_path
) -> None:
    """When ``enable_voter_e`` and ``e_required`` are both True and the
    visual probe rejects two of four candidates, those two are dropped
    from the consensus shots set even though all four pass A/B/C/D."""
    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features
    from splitsmith.ensemble import visual as ensemble_visual

    runtime = _build_stub_runtime()
    # Two candidates above 0.5 (kept by Voter E), two below (vetoed).
    runtime.visual = _StubVisualRuntime([0.9, 0.1, 0.8, 0.05])  # type: ignore[assignment]
    # Calibration must carry a voter_e_threshold for the runtime to act.
    cal = runtime.calibration.model_copy(
        update={
            "voter_e_threshold": 0.5,
            "voter_e_target_recall": 0.95,
            "voter_e_clip_model_id": "stub",
            "voter_e_frame_offsets": [0.0],
            "voter_e_probe_artifact": "stub.joblib",
        }
    )
    # Mirror the threshold into the per-class block so thresholds_for() picks it up.
    if cal.thresholds_by_camera_class:
        for cls, t in cal.thresholds_by_camera_class.items():
            cal.thresholds_by_camera_class[cls] = t.model_copy(
                update={"voter_e_threshold": 0.5}
            )
    runtime.calibration = cal

    fake_shots = [
        Shot(
            shot_number=i + 1,
            time_absolute=5.0 + i * 0.3,
            time_from_beep=i * 0.3,
            split=0.3 if i > 0 else 5.0,
            peak_amplitude=0.4,
            confidence=0.6,
            notes="",
        )
        for i in range(4)
    ]
    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: fake_shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, times, runtime: np.full(
            (len(times), len(CLAP_PROMPTS)), 0.5, dtype=np.float32
        ),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, times, runtime: np.full(len(times), 0.9, dtype=np.float32),
    )
    # Bypass real frame extraction + CLIP forward pass.
    monkeypatch.setattr(
        ensemble_visual,
        "compute_visual_features",
        lambda video_path, source_times, runtime, **_: np.zeros(
            (len(source_times), 1), dtype=np.float32
        ),
    )

    audio = np.zeros(48_000 * 20, dtype=np.float32)
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"")
    result = detect_shots_ensemble(
        audio,
        48_000,
        beep_time=5.0,
        stage_time=10.0,
        runtime=runtime,
        ensemble_config=EnsembleConfig(enable_voter_e=True, e_required=True),
        video_path=fake_video,
        source_beep_time=5.0,
    )
    votes_e = [c.vote_e for c in result.candidates]
    kept = [c.kept for c in result.candidates]
    signals = [c.voter_e_signal for c in result.candidates]
    assert votes_e == [1, 0, 1, 0]
    assert signals == [0.9, 0.1, 0.8, 0.05]
    # Without e_required all four would have passed (A+B+C+D=4 >= 3 + c_required=True).
    # With e_required=True, the two below threshold are dropped.
    assert kept == [True, False, True, False]


def test_candidate_times_in_source_anchors_on_beep() -> None:
    from splitsmith.ensemble.visual import candidate_times_in_source

    clip_times = np.array([0.5, 1.5, 3.0])
    out = candidate_times_in_source(
        clip_times, audit_beep_in_clip=0.5, source_beep_time=22.0
    )
    # clip_t - audit_beep_in_clip + source_beep_time
    assert out.tolist() == [22.0, 23.0, 24.5]


# ---------------------------------------------------------------------------
# Per-camera-class threshold stratification (issue #137)
# ---------------------------------------------------------------------------


def test_voter_c_feature_matrix_appends_camera_class_one_hot() -> None:
    """Issue #139: ``voter_c_feature_matrix`` appends a one-hot block.

    Verifies dimensionality, default fallback, per-row class assignment,
    and that unknown classes fall through to ``headcam`` (column 0).
    """
    from splitsmith.ensemble.features import (
        CAMERA_CLASS_FEATURE_DIM,
        camera_class_one_hot,
    )

    n = 3
    hand = np.zeros((n, HAND_FEATURE_DIM), dtype=np.float64)
    sims = np.zeros((n, len(CLAP_PROMPTS)), dtype=np.float32)
    diffs = np.zeros(n, dtype=np.float32)

    # Default (no camera_class) -> all rows tagged headcam (column 0).
    x_default = voter_c_feature_matrix(hand, sims, diffs)
    assert x_default.shape == (n, VOTER_C_FEATURE_DIM)
    cam_block = x_default[:, -CAMERA_CLASS_FEATURE_DIM:]
    assert np.array_equal(cam_block[:, 0], np.ones(n))
    assert cam_block[:, 1].sum() == 0

    # Single string -> broadcast to every row.
    x_phone = voter_c_feature_matrix(hand, sims, diffs, camera_classes="handheld")
    cam_block = x_phone[:, -CAMERA_CLASS_FEATURE_DIM:]
    assert np.array_equal(cam_block[:, 1], np.ones(n))
    assert cam_block[:, 0].sum() == 0

    # Per-row mix.
    x_mix = voter_c_feature_matrix(
        hand, sims, diffs, camera_classes=["headcam", "handheld", "future-class"]
    )
    cam_block = x_mix[:, -CAMERA_CLASS_FEATURE_DIM:]
    # Unknown class falls back to headcam (column 0).
    assert cam_block[0, 0] == 1.0 and cam_block[0, 1] == 0.0
    assert cam_block[1, 0] == 0.0 and cam_block[1, 1] == 1.0
    assert cam_block[2, 0] == 1.0 and cam_block[2, 1] == 0.0

    # Direct one-hot helper sanity.
    only = camera_class_one_hot("handheld", 4)
    assert only.shape == (4, CAMERA_CLASS_FEATURE_DIM)
    assert (only[:, 1] == 1.0).all()


def test_camera_class_from_mount_maps_known_mounts() -> None:
    from splitsmith.ensemble import (
        CAMERA_CLASS_HANDHELD,
        CAMERA_CLASS_HEADCAM,
        DEFAULT_CAMERA_CLASS,
        camera_class_from_mount,
    )

    assert camera_class_from_mount("head") == CAMERA_CLASS_HEADCAM
    assert camera_class_from_mount("chest") == CAMERA_CLASS_HEADCAM
    assert camera_class_from_mount("helmet") == CAMERA_CLASS_HEADCAM
    assert camera_class_from_mount("belt") == CAMERA_CLASS_HEADCAM
    assert camera_class_from_mount("hand") == CAMERA_CLASS_HANDHELD
    assert camera_class_from_mount("gimbal") == CAMERA_CLASS_HANDHELD
    assert camera_class_from_mount("tripod") == CAMERA_CLASS_HANDHELD
    assert camera_class_from_mount("monopod") == CAMERA_CLASS_HANDHELD
    # Unknown / missing -> default class so callers can pass through
    # whatever the fixture says without guarding.
    assert camera_class_from_mount(None) == DEFAULT_CAMERA_CLASS
    assert camera_class_from_mount("future-mount") == DEFAULT_CAMERA_CLASS


def test_calibration_thresholds_for_class_returns_class_thresholds() -> None:
    """Per-class lookup hits the correct entry; unknown classes fall back."""
    from splitsmith.ensemble.calibration import ClassThresholds

    cal = EnsembleCalibration(
        voter_a_floor=0.05,
        voter_b_threshold=0.0,
        voter_c_threshold=0.5,
        voter_d_threshold=0.0,
        voter_c_target_recall=0.95,
        tolerance_ms=75.0,
        clap_prompts_shot=list(CLAP_PROMPTS_SHOT),
        clap_prompts=list(CLAP_PROMPTS),
        calibration_fixtures=["a", "b"],
        n_calibration_candidates=10,
        n_calibration_positives=5,
        voter_c_feature_dim=VOTER_C_FEATURE_DIM,
        built_at="1970-01-01T00:00:00Z",
        default_camera_class="headcam",
        thresholds_by_camera_class={
            "headcam": ClassThresholds(
                voter_a_floor=0.05,
                voter_b_threshold=0.0,
                voter_c_threshold=0.5,
                voter_d_threshold=0.0,
                n_calibration_candidates=8,
                n_calibration_positives=4,
                calibration_fixtures=["a"],
            ),
            "handheld": ClassThresholds(
                voter_a_floor=0.10,
                voter_b_threshold=0.20,
                voter_c_threshold=0.30,
                voter_d_threshold=0.40,
                n_calibration_candidates=2,
                n_calibration_positives=1,
                calibration_fixtures=["b"],
            ),
        },
    )

    assert cal.thresholds_for("headcam").voter_c_threshold == 0.5
    assert cal.thresholds_for("handheld").voter_c_threshold == 0.30
    # Unknown class -> default.
    assert cal.thresholds_for("future-class").voter_c_threshold == 0.5
    # No class given -> default.
    assert cal.thresholds_for(None).voter_c_threshold == 0.5


def test_calibration_thresholds_for_legacy_artifact_synthesizes_single_class() -> None:
    """Pre-issue-#137 artifacts (no per-class dict) still resolve to a single set."""
    cal = EnsembleCalibration(
        voter_a_floor=0.07,
        voter_b_threshold=0.08,
        voter_c_threshold=0.42,
        voter_d_threshold=0.09,
        voter_c_target_recall=0.95,
        tolerance_ms=75.0,
        clap_prompts_shot=list(CLAP_PROMPTS_SHOT),
        clap_prompts=list(CLAP_PROMPTS),
        calibration_fixtures=["legacy"],
        n_calibration_candidates=1,
        n_calibration_positives=1,
        voter_c_feature_dim=VOTER_C_FEATURE_DIM,
        built_at="1970-01-01T00:00:00Z",
    )

    th = cal.thresholds_for("anything-at-all")
    assert th.voter_a_floor == 0.07
    assert th.voter_c_threshold == 0.42


def test_detect_shots_ensemble_uses_per_class_thresholds(monkeypatch) -> None:
    """A handheld-class detection picks up the handheld voter A floor.

    Headcam threshold (0.05) would let confidence=0.07 pass voter A;
    handheld threshold (0.20) rejects it. The test asserts that passing
    ``camera_class="handheld"`` flips voter A from yes to no on the same
    candidate without changing any other state.
    """
    from splitsmith.ensemble import api as ensemble_api
    from splitsmith.ensemble import features as ensemble_features
    from splitsmith.ensemble.calibration import ClassThresholds

    runtime = _build_stub_runtime()
    runtime.calibration = runtime.calibration.model_copy(
        update={
            "default_camera_class": "headcam",
            "thresholds_by_camera_class": {
                "headcam": ClassThresholds(
                    voter_a_floor=0.05,
                    voter_b_threshold=0.0,
                    voter_c_threshold=0.5,
                    voter_d_threshold=0.0,
                    n_calibration_candidates=10,
                    n_calibration_positives=5,
                    calibration_fixtures=["stub"],
                ),
                "handheld": ClassThresholds(
                    voter_a_floor=0.20,
                    voter_b_threshold=0.0,
                    voter_c_threshold=0.5,
                    voter_d_threshold=0.0,
                    n_calibration_candidates=5,
                    n_calibration_positives=2,
                    calibration_fixtures=["stub-phone"],
                ),
            },
        }
    )

    fake_shots = [
        Shot(
            shot_number=1,
            time_absolute=5.5,
            time_from_beep=0.5,
            split=0.5,
            peak_amplitude=0.4,
            confidence=0.07,
            notes="",
        )
    ]
    monkeypatch.setattr(ensemble_api, "detect_shots", lambda *a, **kw: fake_shots)
    monkeypatch.setattr(
        ensemble_features,
        "compute_clap_similarities",
        lambda audio, sr, times, runtime: np.full(
            (len(times), len(CLAP_PROMPTS)), 0.5, dtype=np.float32
        ),
    )
    monkeypatch.setattr(
        ensemble_features,
        "compute_pann_gunshot_probs",
        lambda audio, sr, times, runtime: np.full(len(times), 0.9, dtype=np.float32),
    )

    audio = np.zeros(48_000 * 20, dtype=np.float32)

    # Headcam class (default): floor 0.05 -> confidence 0.07 passes voter A.
    result_head = detect_shots_ensemble(
        audio, 48_000, beep_time=5.0, stage_time=10.0, runtime=runtime
    )
    assert result_head.candidates[0].vote_a == 1

    # Handheld class: floor 0.20 -> 0.07 rejected by voter A.
    result_hand = detect_shots_ensemble(
        audio,
        48_000,
        beep_time=5.0,
        stage_time=10.0,
        runtime=runtime,
        camera_class="handheld",
    )
    assert result_hand.candidates[0].vote_a == 0
