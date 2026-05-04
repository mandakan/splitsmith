"""Tests for scripts/extract_full_fixture_audio.py.

The script is dev-only orchestration -- pure helpers go through unit tests
that mock the ffmpeg subprocess; an integration test (marked) runs real
ffmpeg against tests/fixtures/stage_sample.mp4.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_module():
    """Load the orchestrator script as a module; scripts/ is not a package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "extract_full_fixture_audio.py"
    spec = importlib.util.spec_from_file_location("extract_full_fixture_audio", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


extractor = _load_module()


def test_compute_window_whole_video_when_pads_omitted() -> None:
    start, end = extractor.compute_window(
        duration=300.0, fixture_window=(40.0, 90.0), pre_pad=None, post_pad=None
    )
    assert start == 0.0
    assert end == 300.0


def test_compute_window_clips_to_source_bounds() -> None:
    start, end = extractor.compute_window(
        duration=120.0, fixture_window=(10.0, 60.0), pre_pad=30.0, post_pad=120.0
    )
    # pre_pad would have started at -20.0 -> clipped to 0.0.
    # post_pad would have ended at 180.0 -> clipped to source duration.
    assert start == 0.0
    assert end == 120.0


def test_compute_window_widens_symmetrically() -> None:
    start, end = extractor.compute_window(
        duration=300.0, fixture_window=(50.0, 100.0), pre_pad=10.0, post_pad=20.0
    )
    assert start == pytest.approx(40.0)
    assert end == pytest.approx(120.0)


def test_resolve_video_uses_overrides_first(tmp_path: Path) -> None:
    src = tmp_path / "override.mp4"
    src.write_bytes(b"")
    sources = {
        "video_dir": "/nope",
        "fixtures": {"stage-shots-x": "ignored.mp4"},
        "overrides": {"stage-shots-x": str(src)},
    }
    assert extractor.resolve_video("stage-shots-x", sources) == src


def test_resolve_video_joins_fixtures_with_video_dir(tmp_path: Path) -> None:
    video_dir = tmp_path / "Camera01"
    video_dir.mkdir()
    src = video_dir / "VID_X.mp4"
    src.write_bytes(b"")
    sources = {
        "video_dir": str(video_dir),
        "fixtures": {"stage-shots-x": "VID_X.mp4"},
    }
    assert extractor.resolve_video("stage-shots-x", sources) == src


def test_resolve_video_raises_when_video_missing(tmp_path: Path) -> None:
    sources = {
        "video_dir": str(tmp_path),
        "fixtures": {"stage-shots-x": "missing.mp4"},
    }
    with pytest.raises(extractor.ExtractError):
        extractor.resolve_video("stage-shots-x", sources)


def test_resolve_video_raises_when_no_entry() -> None:
    with pytest.raises(extractor.ExtractError):
        extractor.resolve_video("stage-shots-unknown", {"video_dir": "/x", "fixtures": {}})


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_run_ffmpeg_extract_builds_mono_48k_command(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"")
    dst = tmp_path / "out.wav"
    runner = _RecordingRunner()
    extractor.run_ffmpeg_extract(
        src, dst, start=12.0, duration=30.0, overwrite=True, runner=runner
    )
    cmd = runner.calls[0]
    # -ss before -i for fast seek, mono 48k PCM.
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx
    assert cmd[ss_idx + 1] == "12.000"
    assert cmd[cmd.index("-t") + 1] == "30.000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert "-y" in cmd  # overwrite=True


def test_extract_one_writes_sidecar_with_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures_dir = tmp_path / "fixtures"
    full_dir = fixtures_dir / "full"
    fixtures_dir.mkdir()
    full_dir.mkdir()
    monkeypatch.setattr(extractor, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(extractor, "FULL_DIR", full_dir)
    monkeypatch.setattr(extractor, "PROBE_CACHE_DIR", tmp_path / "probe")

    audit = {
        "fixture_window_in_source": [22.4417, 67.2017],
        "beep_time": 0.5,
        "stage_time_seconds": 42.76,
    }
    (fixtures_dir / "stage-shots-x.json").write_text(json.dumps(audit))
    src = tmp_path / "VID_X.mp4"
    src.write_bytes(b"")
    sources = {"video_dir": str(tmp_path), "fixtures": {"stage-shots-x": "VID_X.mp4"}}

    monkeypatch.setattr(
        extractor, "probe", lambda path, cache_dir: type("R", (), {"duration": 180.0})()
    )
    runner = _RecordingRunner()
    sidecar = extractor.extract_one(
        "stage-shots-x",
        sources,
        pre_pad=None,
        post_pad=None,
        overwrite=True,
        log=lambda _msg: None,
        runner=runner,
    )

    assert sidecar["full_window_in_source"] == [0.0, 180.0]
    assert sidecar["sample_rate"] == 48000
    assert sidecar["fixture_window_in_source"] == [22.4417, 67.2017]
    assert (full_dir / "stage-shots-x_full.json").exists()
    # ffmpeg called once with the whole-video window.
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[cmd.index("-ss") + 1] == "0.000"
    assert cmd[cmd.index("-t") + 1] == "180.000"
