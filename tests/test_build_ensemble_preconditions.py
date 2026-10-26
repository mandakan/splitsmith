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
