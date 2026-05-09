"""End-to-end smoke test for ``splitsmith compare export <manifest>``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from splitsmith.cli import app
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _ffmpeg_stub_factory() -> Any:
    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return stub


def _seed_shooter(root: Path, *, name: str, stage_name: str = "Skipper") -> Path:
    project = MatchProject.init(root, name=name)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name=stage_name,
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(root)
    trim = project.exports_path(root) / f"stage1_{_slugify(stage_name)}_trimmed.mp4"
    trim.parent.mkdir(parents=True, exist_ok=True)
    trim.write_bytes(b"")
    return root


def test_export_writes_fcpxml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a_root = _seed_shooter(tmp_path / "a", name="a")
    b_root = _seed_shooter(tmp_path / "b", name="b")

    manifest_path = tmp_path / "compare.yaml"
    output = tmp_path / "out.fcpxml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(output),
                "audio_from": "Mathias",
                "shooters": [
                    {"project": str(a_root), "label": "Anders"},
                    {"project": str(b_root), "label": "Mathias"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Stub ffprobe (used by the project loader) and ffmpeg (used by the
    # filler renderer) so the test doesn't depend on either binary.
    def fake_probe(_p: Path) -> VideoMetadata:
        return VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=30.0,
            frame_rate_num=30,
            frame_rate_den=1,
        )

    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", fake_probe)
    monkeypatch.setattr(em_mod.subprocess, "run", _ffmpeg_stub_factory())

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path)])
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_missing_manifest_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0


def test_audio_from_mismatch_surfaces_validation_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bad.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(tmp_path / "out.fcpxml"),
                "audio_from": "NotPresent",
                "shooters": [{"project": str(tmp_path / "p"), "label": "Real"}],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path)])
    assert result.exit_code != 0
