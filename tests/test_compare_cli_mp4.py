"""``splitsmith compare export --format mp4`` CLI wiring.

The renderer itself (``mp4_grid.render_grid_mp4``) is fully tested in
``test_compare_mp4_grid_render.py``. These tests only cover the CLI's job:
validating the flag, routing to the right emitter, owning + cleaning up
the scratch work dir, and reporting progress. Real ffmpeg is stubbed out
via ``subprocess.run`` the same way ``test_compare_cli.py`` stubs the
FCPXML path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import splitsmith.compare.cli as cli_mod
import splitsmith.compare.project_loader as pl_mod
from splitsmith.cli import app
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter, ShooterStageData
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from tests.conftest import strip_ansi

runner = CliRunner()


def _ffmpeg_stub_factory() -> Any:
    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return stub


def _fake_probe(_p: Path) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _seed_match_with_stages(root: Path, *, stage_count: int = 2) -> Path:
    """A merged match, one shooter, ``stage_count`` stages, all trimmed on disk."""
    stage_names = {n: f"Stage {n}" for n in range(1, stage_count + 1)}
    match = Match.init(root, name="Compare Match")
    match.stages = [MatchStageDefinition(stage_number=n, stage_name=name) for n, name in stage_names.items()]
    match.save(root)

    def _videos() -> list[StageVideo]:
        return [StageVideo(path=Path("raw/v.mov"), role="primary", beep_time=5.0)]

    match.add_shooter(
        root,
        Shooter(
            slug="mathias",
            name="Mathias",
            stages=[
                ShooterStageData(stage_number=n, time_seconds=10.0, videos=_videos()) for n in stage_names
            ],
        ),
    )

    shooter_root = Match.shooter_root(root, "mathias")
    project = MatchProject.init(shooter_root, name=match.name)
    project.stages = [
        StageEntry(stage_number=n, stage_name=name, time_seconds=10.0, videos=_videos())
        for n, name in stage_names.items()
    ]
    project.save(shooter_root)

    exports = project.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    for n, name in stage_names.items():
        (exports / f"stage{n}_{_slugify(name)}_trimmed.mp4").write_bytes(b"")
    return root


def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)


# --- flag plumbing ---------------------------------------------------------


def test_format_flag_is_documented() -> None:
    result = runner.invoke(app, ["compare", "export", "--help"])
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "--format" in output
    assert "mp4" in output


def test_mp4_format_rejects_a_manifest_source(tmp_path: Path) -> None:
    manifest = tmp_path / "m.yaml"
    manifest.write_text("output: out.fcpxml\naudio_from: A\nshooters: []\n", encoding="utf-8")
    result = runner.invoke(
        app, ["compare", "export", str(manifest), "--format", "mp4", "-o", str(tmp_path / "o.mp4")]
    )
    assert result.exit_code == 2
    assert "match folder" in result.output


def test_unknown_format_rejected(tmp_path: Path) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "avi",
            "-o",
            str(tmp_path / "o.avi"),
        ],
    )
    assert result.exit_code == 2
    assert "--format" in result.output


# --- routing -----------------------------------------------------------


def test_fcpxml_format_still_routes_to_the_emitter_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (and explicit) fcpxml must never touch mp4_grid."""
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.fcpxml"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.emitter_mod.subprocess, "run", _ffmpeg_stub_factory())

    render_calls: list[Any] = []
    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", lambda *a, **kw: render_calls.append((a, kw)))

    result = runner.invoke(
        app, ["compare", "export", str(match_root), "--audio-from", "mathias", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert render_calls == []


def test_mp4_format_calls_render_grid_mp4_not_the_emitter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    emit_calls: list[Any] = []
    monkeypatch.setattr(
        cli_mod.emitter_mod, "emit_compare_fcpxml", lambda *a, **kw: emit_calls.append((a, kw))
    )
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert emit_calls == []
    assert output.exists()


def test_mp4_render_receives_the_loaded_bundles_and_resolved_audio_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    seen: dict[str, Any] = {}
    real_render = cli_mod.mp4_grid.render_grid_mp4

    def spy(shooters, *, audio_label, output_path, **kwargs):
        seen["labels"] = sorted(s.label for s in shooters)
        seen["audio_label"] = audio_label
        seen["output_path"] = output_path
        return real_render(shooters, audio_label=audio_label, output_path=output_path, **kwargs)

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", spy)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["labels"] == ["Mathias"]
    assert seen["audio_label"] == "Mathias"
    assert seen["output_path"] == output


# --- work dir ownership --------------------------------------------------


def test_work_dir_does_not_survive_a_successful_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    leftovers = [p for p in output.parent.iterdir() if p.name.startswith(".compare-grid-work")]
    assert leftovers == []


def test_work_dir_does_not_survive_a_failed_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def _always_fails(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(cli_mod.subprocess, "run", _always_fails)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 1, result.output
    assert not output.exists()
    leftovers = [p for p in output.parent.iterdir() if p.name.startswith(".compare-grid-work")]
    assert leftovers == []


# --- progress --------------------------------------------------------------


def test_mp4_render_prints_per_stage_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=2)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    # One message per stage, in order, each naming its place out of the total --
    # a full 4K match render has no other sign of life for minutes at a time.
    stage1 = result.output.find("stage 1")
    stage2 = result.output.find("stage 2")
    assert stage1 != -1 and stage2 != -1, result.output
    assert stage1 < stage2
    assert "1 of 2" in result.output
    assert "2 of 2" in result.output
