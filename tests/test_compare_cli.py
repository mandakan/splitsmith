"""End-to-end smoke test for ``splitsmith compare export <manifest>``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from splitsmith.cli import app
from splitsmith.compare.project_loader import CompareShooterBundle, ProbeFn
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter, ShooterStageData
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


def _fake_probe(_p: Path) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _cams() -> list[StageVideo]:
    """A helmet primary + a chest secondary, both beeped."""
    return [
        StageVideo(path=Path("raw/helmet.mov"), role="primary", camera_mount="helmet", beep_time=5.0),
        StageVideo(path=Path("raw/chest.mov"), role="secondary", camera_mount="chest", beep_time=6.0),
    ]


def _seed_match_with_two_cams(root: Path, *, stage_name: str = "Skipper") -> Path:
    """A one-stage merged match with one shooter (slug ``mathias``) on two cams.

    Both cams' lossless trims are on disk, so either camera selection
    yields a complete grid.
    """
    match = Match.init(root, name="Compare Match")
    match.stages = [MatchStageDefinition(stage_number=1, stage_name=stage_name)]
    match.save(root)
    match.add_shooter(
        root,
        Shooter(
            slug="mathias",
            name="Mathias",
            stages=[ShooterStageData(stage_number=1, time_seconds=10.0, videos=_cams())],
        ),
    )

    shooter_root = Match.shooter_root(root, "mathias")
    project = MatchProject.init(shooter_root, name=match.name)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name=stage_name,
            time_seconds=10.0,
            videos=_cams(),
        )
    ]
    project.save(shooter_root)

    # Reload for stamped stage numbers -> real video_ids in the trim names.
    stamped = MatchProject.load(shooter_root)
    exports = stamped.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    base = f"stage1_{_slugify(stage_name)}"
    (exports / f"{base}_trimmed.mp4").write_bytes(b"")
    chest_id = stamped.stage(1).videos[-1].video_id
    (exports / f"{base}_cam_{chest_id}_trimmed.mp4").write_bytes(b"")
    return root


def test_camera_flag_reaches_the_match_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--camera SLUG=VALUE`` on the match path lands on the loader call."""
    match_root = _seed_match_with_two_cams(tmp_path / "match")
    output = tmp_path / "out.fcpxml"

    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    real = pl_mod.load_shooter_from_match
    seen: list[str | None] = []

    def spy(
        match_root_arg: Path,
        slug: str,
        label: str,
        *,
        camera: str | None = None,
        probe: ProbeFn | None = None,
    ) -> CompareShooterBundle:
        seen.append(camera)
        return real(match_root_arg, slug, label, camera=camera, probe=_fake_probe)

    monkeypatch.setattr(pl_mod, "load_shooter_from_match", spy)
    monkeypatch.setattr(em_mod.subprocess, "run", _ffmpeg_stub_factory())

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--output",
            str(output),
            "--camera",
            "mathias=chest",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == ["chest"]
    assert output.exists()


def test_camera_flag_rejects_malformed_pair(tmp_path: Path) -> None:
    match_root = _seed_match_with_two_cams(tmp_path / "match")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--output",
            str(tmp_path / "out.fcpxml"),
            "--camera",
            "chest",
        ],
    )
    assert result.exit_code == 2
    assert "SLUG=VALUE" in result.output


def test_unresolvable_camera_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A selector that matches nothing anywhere is a config error, not a traceback."""
    match_root = _seed_match_with_two_cams(tmp_path / "match")

    import splitsmith.compare.project_loader as pl_mod

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--output",
            str(tmp_path / "out.fcpxml"),
            "--camera",
            "mathias=backpack",
        ],
    )
    assert result.exit_code == 2
    assert "backpack" in result.output


def test_camera_flag_rejects_unknown_slug(tmp_path: Path) -> None:
    """A slug that names no shooter would otherwise export the wrong camera silently."""
    match_root = _seed_match_with_two_cams(tmp_path / "match")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--output",
            str(tmp_path / "out.fcpxml"),
            "--camera",
            "anders=chest",
        ],
    )
    assert result.exit_code == 2
    assert "anders" in result.output


def test_manifest_camera_reaches_the_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest's per-shooter ``camera:`` key lands on ``load_shooter``."""
    a_root = _seed_shooter(tmp_path / "a", name="a")
    manifest_path = tmp_path / "compare.yaml"
    output = tmp_path / "out.fcpxml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(output),
                "audio_from": "Anders",
                "shooters": [{"project": str(a_root), "label": "Anders", "camera": "primary"}],
            }
        ),
        encoding="utf-8",
    )

    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    real = pl_mod.load_shooter
    seen: list[str | None] = []

    def spy(
        project_root: Path,
        label: str,
        *,
        camera: str | None = None,
        probe: ProbeFn | None = None,
    ) -> CompareShooterBundle:
        seen.append(camera)
        return real(project_root, label, camera=camera, probe=_fake_probe)

    monkeypatch.setattr(pl_mod, "load_shooter", spy)
    monkeypatch.setattr(em_mod.subprocess, "run", _ffmpeg_stub_factory())

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path)])
    assert result.exit_code == 0, result.output
    assert seen == ["primary"]


def _seed_shooter_with_two_cams(root: Path, *, name: str, stage_name: str = "Skipper") -> Path:
    """A legacy single-shooter project on a helmet primary + a chest secondary.

    Both cams' lossless trims are on disk, so either selector resolves.
    """
    project = MatchProject.init(root, name=name)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name=stage_name,
            time_seconds=10.0,
            videos=_cams(),
        )
    ]
    project.save(root)

    stamped = MatchProject.load(root)
    exports = stamped.exports_path(root)
    exports.mkdir(parents=True, exist_ok=True)
    base = f"stage1_{_slugify(stage_name)}"
    (exports / f"{base}_trimmed.mp4").write_bytes(b"")
    chest_id = stamped.stage(1).videos[-1].video_id
    (exports / f"{base}_cam_{chest_id}_trimmed.mp4").write_bytes(b"")
    return root


def _spy_camera_by_label(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """Record the ``camera`` each ``load_shooter`` call received, by label."""
    import splitsmith.compare.project_loader as pl_mod

    real = pl_mod.load_shooter
    seen: dict[str, str | None] = {}

    def spy(
        project_root: Path,
        label: str,
        *,
        camera: str | None = None,
        probe: ProbeFn | None = None,
    ) -> CompareShooterBundle:
        seen[label] = camera
        return real(project_root, label, camera=camera, probe=_fake_probe)

    monkeypatch.setattr(pl_mod, "load_shooter", spy)
    return seen


def test_camera_flag_overrides_manifest_camera_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI beats YAML for the named shooter; the others keep their key."""
    anders = _seed_shooter_with_two_cams(tmp_path / "anders", name="anders")
    mathias = _seed_shooter_with_two_cams(tmp_path / "mathias", name="mathias")
    manifest_path = tmp_path / "compare.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(tmp_path / "out.fcpxml"),
                "audio_from": "Anders",
                "shooters": [
                    {"project": str(anders), "label": "Anders", "camera": "helmet"},
                    {"project": str(mathias), "label": "Mathias", "camera": "helmet"},
                ],
            }
        ),
        encoding="utf-8",
    )

    import splitsmith.compare.emitter as em_mod

    seen = _spy_camera_by_label(monkeypatch)
    monkeypatch.setattr(em_mod.subprocess, "run", _ffmpeg_stub_factory())

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path), "--camera", "anders=chest"])
    assert result.exit_code == 0, result.output
    assert seen["Anders"] == "chest"
    assert seen["Mathias"] == "helmet"


def test_audio_from_flag_overrides_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a_root = _seed_shooter(tmp_path / "a", name="a")
    b_root = _seed_shooter(tmp_path / "b", name="b")
    manifest_path = tmp_path / "compare.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(tmp_path / "out.fcpxml"),
                "audio_from": "Anders",
                "shooters": [
                    {"project": str(a_root), "label": "Anders"},
                    {"project": str(b_root), "label": "Mathias"},
                ],
            }
        ),
        encoding="utf-8",
    )

    import splitsmith.compare.cli as cli_mod
    import splitsmith.compare.project_loader as pl_mod

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    seen: list[str] = []

    def spy_emit(
        *, manifest: Any, shooters: list[CompareShooterBundle], output_path: Path, **_kw: Any
    ) -> None:
        seen.append(manifest.audio_from)

    monkeypatch.setattr(cli_mod.emitter_mod, "emit_compare_fcpxml", spy_emit)

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path), "--audio-from", "Mathias"])
    assert result.exit_code == 0, result.output
    assert seen == ["Mathias"]


def test_audio_from_flag_unknown_label_exits_2(tmp_path: Path) -> None:
    a_root = _seed_shooter(tmp_path / "a", name="a")
    manifest_path = tmp_path / "compare.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": str(tmp_path / "out.fcpxml"),
                "audio_from": "Anders",
                "shooters": [{"project": str(a_root), "label": "Anders"}],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path), "--audio-from", "Nobody"])
    assert result.exit_code == 2
    assert "Anders" in result.output


def test_output_flag_overrides_manifest_and_resolves_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative --output lands where the user stands, not next to the YAML."""
    a_root = _seed_shooter(tmp_path / "a", name="a")
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "compare.yaml"
    manifest_output = manifest_dir / "from-yaml.fcpxml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "output": "from-yaml.fcpxml",
                "audio_from": "Anders",
                "shooters": [{"project": str(a_root), "label": "Anders"}],
            }
        ),
        encoding="utf-8",
    )

    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(em_mod.subprocess, "run", _ffmpeg_stub_factory())

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    runner = CliRunner()
    result = runner.invoke(app, ["compare", "export", str(manifest_path), "--output", "from-flag.fcpxml"])
    assert result.exit_code == 0, result.output
    assert (cwd / "from-flag.fcpxml").exists()
    assert not (manifest_dir / "from-flag.fcpxml").exists()
    assert not manifest_output.exists()


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
