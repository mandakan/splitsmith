"""Calibration-build precondition failures must be loud and actionable.

The rebuild_calibration hang (2026-08-17): every fixture was skipped for
missing CLAP/PANN feature caches, the universe came out empty, and the
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


def test_empty_universe_raises_builderror_naming_the_extract_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _build_mod()
    fixtures_dir = tmp_path / "fixtures"
    cache_dir = fixtures_dir / ".cache"
    fixtures_dir.mkdir()
    # One real-looking fixture with audio but no CLAP/PANN cache -- the
    # exact state of a fresh checkout / cleaned cache.
    (fixtures_dir / "stage-shots-hfo-masters-2026-stage1-s0fe3d797.json").write_text(
        json.dumps({"beep_time": 1.0, "stage_time_seconds": 10.0, "shots": [{"time": 2.0}]})
    )
    (fixtures_dir / "stage-shots-hfo-masters-2026-stage1-s0fe3d797.wav").write_bytes(b"")
    monkeypatch.setattr(mod, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(mod, "CACHE_DIR", cache_dir)

    with pytest.raises(mod.BuildError) as exc:
        mod.build_artifacts(fixtures=["stage-shots-hfo-masters-2026-stage1-s0fe3d797"])

    msg = str(exc.value)
    assert "extract_clap_features.py" in msg
    assert "extract_audio_embeddings.py" in msg


def test_builderror_is_a_real_exception_not_systemexit() -> None:
    mod = _build_mod()
    # The job runner's handler catches Exception; SystemExit would slip
    # past a bare ``except Exception`` on a worker thread.
    assert issubclass(mod.BuildError, Exception)
    assert not issubclass(mod.BuildError, SystemExit)
