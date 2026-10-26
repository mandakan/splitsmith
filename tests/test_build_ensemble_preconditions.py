"""Calibration-build precondition failures must be loud and actionable.

The rebuild_calibration hang (2026-08-17): every fixture was skipped for
missing CLAP feature caches, the universe came out empty, and the
build bailed via ``raise SystemExit`` -- which a job-thread runner
couldn't catch, so the job read "running" forever. These pin the two
script-side halves of the fix: preconditions raise ``BuildError`` (a
real exception), and an all-skipped corpus names the extraction scripts
instead of the opaque "no camera class produced calibrated thresholds".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _build_mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import build_ensemble_artifacts as mod
    finally:
        sys.path.pop(0)
    return mod


def test_missing_feature_caches_raise_builderror_naming_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _build_mod()
    import feature_caches

    fixtures_dir = tmp_path / "fixtures"
    cache_dir = fixtures_dir / ".cache"
    fixtures_dir.mkdir()
    # One real-looking fixture with audio but no CLAP cache -- the
    # exact state of a fresh checkout / cleaned cache. The build now stops
    # in its preflight, before any detection, and says how to fix it.
    stem = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    (fixtures_dir / f"{stem}.json").write_text(
        json.dumps({"beep_time": 1.0, "stage_time_seconds": 10.0, "shots": [{"time": 2.0}]}), encoding="utf-8"
    )
    (fixtures_dir / f"{stem}.wav").write_bytes(b"")
    monkeypatch.setattr(mod, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(feature_caches, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(mod, "ensure_audio", lambda fixtures, log=print: dict.fromkeys(fixtures, "0" * 64))

    with pytest.raises(mod.BuildError) as exc:
        mod.build_artifacts(fixtures=[stem])

    msg = str(exc.value)
    assert "--extract" in msg
    assert stem in msg


def test_builderror_is_a_real_exception_not_systemexit() -> None:
    mod = _build_mod()
    # The job runner's handler catches Exception; SystemExit would slip
    # past a bare ``except Exception`` on a worker thread.
    assert issubclass(mod.BuildError, Exception)
    assert not issubclass(mod.BuildError, SystemExit)


def test_model_artifacts_block_carries_across_rebuilds(tmp_path: Path) -> None:
    """The slim-install download block is curated by the export/upload
    scripts, not the calibration build -- a rebuild must carry it
    forward, not drop it (broke ``fetch-models`` on slim wheels,
    2026-08-17)."""
    mod = _build_mod()
    cal_path = tmp_path / "ensemble_calibration.json"
    cal_path.write_text(
        json.dumps({"voter_a_floor": 0.1, "model_artifacts": {"clap_audio_encoder": {"sha256": "x"}}}),
        encoding="utf-8",
    )
    assert mod._carry_model_artifacts(cal_path) == {"clap_audio_encoder": {"sha256": "x"}}
    # No previous file / no block -> nothing to carry.
    assert mod._carry_model_artifacts(tmp_path / "missing.json") is None
    cal_path.write_text(json.dumps({"voter_a_floor": 0.1}), encoding="utf-8")
    assert mod._carry_model_artifacts(cal_path) is None


def test_the_carried_artifact_block_keeps_only_what_a_model_set_downloads(tmp_path) -> None:
    """The shipped block named the retired PANN export; a rebuild must not carry it."""
    b = _build_mod()
    entry = {"filename": "x.onnx", "sha256": "ab", "size_bytes": 1, "url": "https://example.invalid/x"}
    cal = {
        "model_artifacts": {
            "pann_cnn14": entry,
            "clap_audio_encoder": entry,
            "clap_text_embeddings": entry,
        }
    }
    path = tmp_path / "ensemble_calibration.json"
    path.write_text(json.dumps(cal), encoding="utf-8")
    carried = b._carry_model_artifacts(path)
    assert carried is not None
    assert set(carried) == {"clap_audio_encoder", "clap_text_embeddings"}


def _tiny_universe(monkeypatch, b) -> list[dict]:
    """Two classes, two matches, two stages each; separable enough to fit."""
    import numpy as np

    from splitsmith.ensemble import features as feat

    rng = np.random.default_rng(0)
    rows: list[dict] = []
    for cls in ("headcam", "handheld"):
        for match in ("alpha", "bravo"):
            for stage in (1, 2, 3):
                fix = f"{cls}-{match}-{stage}"
                for i in range(8):
                    label = int(i % 2 == 0)
                    hand = rng.random(feat.HAND_FEATURE_DIM) + label
                    sims = rng.random(len(feat.CLAP_PROMPTS))
                    rows.append(
                        {
                            "fixture": fix,
                            "candidate_idx": i,
                            "t_absolute": 2.0 + i * 0.3,
                            "t_shot": 2.0 + i * 0.3,
                            "camera_class": cls,
                            "label": label,
                            "confidence": float(hand[0]),
                            "peak_amp": float(0.1 + label),
                            "clap_diff": float(label - 0.5),
                            "hand_feats": hand.tolist(),
                            "clap_sims": sims.tolist(),
                        }
                    )
    groups = {r["fixture"]: (r["fixture"].split("-")[1], "-".join(r["fixture"].split("-")[1:])) for r in rows}
    monkeypatch.setattr(b, "_event_groups", lambda fixture: groups[fixture])
    return rows


def test_both_sets_share_one_fixture_list_and_the_build_refuses_otherwise(monkeypatch) -> None:
    from splitsmith.ensemble import features as feat

    b = _build_mod()
    by_class = b._split_by_camera_class(_tiny_universe(monkeypatch, b))
    base = b._train_model_set("base", by_class, target_recall=0.95, log=lambda s: None)
    clap = b._train_model_set("clap", by_class, target_recall=0.95, log=lambda s: None)
    assert base.block["columns"] == list(feat.model_set_columns("base"))
    assert base.block["voter_c_feature_dim"] == len(feat.model_set_columns("base"))
    assert clap.block["voter_c_feature_dim"] == feat.VOTER_C_FEATURE_DIM
    assert base.block["requires_artifacts"] == []
    assert clap.block["requires_artifacts"] == ["clap_audio_encoder", "clap_text_embeddings"]
    assert base.block["thresholds_by_camera_class"]["headcam"]["voter_b_threshold"] is None
    assert clap.block["thresholds_by_camera_class"]["headcam"]["voter_b_threshold"] is not None
    assert {m.n_features_in_ for m in base.models.values()} == {len(feat.model_set_columns("base"))}
    assert {r["model_set"] for r in base.heldout_rows} == {"base"}
    b._assert_sets_agree({"base": base.block, "clap": clap.block})
    clap.block["thresholds_by_camera_class"]["headcam"]["calibration_fixtures"] = ["other"]
    with pytest.raises(b.BuildError, match="fixture"):
        b._assert_sets_agree({"base": base.block, "clap": clap.block})


def test_each_class_gets_its_own_decoder_on_its_set_s_features(monkeypatch) -> None:
    from splitsmith.ensemble.decoder import DECODER_FEATURES_BY_SET

    b = _build_mod()
    universe = _tiny_universe(monkeypatch, b)
    by_class = b._split_by_camera_class(universe)
    stages = {
        r["fixture"]: {"truth": [], "beep": 1.0, "stage_time": 5.0, "duration": 10.0, "expected": None}
        for r in universe
    }
    for r in universe:
        if r["label"]:
            stages[r["fixture"]]["truth"].append(r["t_shot"])
    monkeypatch.setattr(b, "_decoder_stages", lambda fixtures, universe: {f: stages[f] for f in fixtures})
    for name in ("base", "clap"):
        built = b._train_model_set(name, by_class, target_recall=0.95, log=lambda s: None)
        decoders = b._fit_decoders_by_class(
            name,
            universe,
            built.heldout_rows,
            built.block["thresholds_by_camera_class"],
            75.0,
            None,
            lambda s: None,
        )
        assert set(decoders) == {"headcam", "handheld"}
        for decoder in decoders.values():
            assert decoder.features == list(DECODER_FEATURES_BY_SET[name])
            assert decoder.fit_report["fixtures"] == 6


def test_a_graph_whose_width_differs_from_its_set_is_refused() -> None:
    b = _build_mod()

    class Model:
        n_features_in_ = 55

    with pytest.raises(b.BuildError, match="base"):
        b._check_graph_width("base", "headcam", Model(), {"voter_c_feature_dim": 33})
