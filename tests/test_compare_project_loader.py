"""Tests for the per-shooter project loader in compare/project_loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.compare.project_loader import (
    audit_path_for_stage,
    load_shooter,
    load_shooter_from_match,
    trim_path_for_stage,
)
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter, ShooterStageData
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _meta(duration: float = 30.0) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=duration,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _build_project(
    root: Path,
    *,
    name: str = "test",
    pre_buffer: float = 5.0,
    stages: list[StageEntry] | None = None,
) -> MatchProject:
    project = MatchProject.init(root, name=name)
    project.trim_pre_buffer_seconds = pre_buffer
    project.stages = stages or []
    project.save(root)
    return project


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _stub_probe(_path: Path) -> VideoMetadata:
    """Probe stub that returns a constant VideoMetadata for any trim.

    Copied from ``tests/test_compare_merged_match.py`` rather than imported
    across test modules.
    """
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _build_two_stage_match(tmp_path: Path) -> Path:
    """Build a two-stage merged match with one shooter, ``mathias``.

    Mirrors the on-disk state left right after a merge (see
    ``execute_merge``): ``shooter.json`` (the match-aware form) and
    ``project.json`` (the legacy compat shim merge writes alongside it)
    agree with each other -- stage 1 is fully beeped with its lossless
    trim on disk, stage 2 has a primary video assigned but no beep yet.
    """
    match_root = tmp_path / "match"
    match = Match.init(match_root, name="Two Stage Classic")
    match.stages = [
        MatchStageDefinition(stage_number=1, stage_name="Stage One"),
        MatchStageDefinition(stage_number=2, stage_name="Stage Two"),
    ]
    match.save(match_root)

    shooter = Shooter(
        slug="mathias",
        name="Mathias",
        stages=[
            ShooterStageData(
                stage_number=1,
                time_seconds=11.0,
                videos=[
                    StageVideo(
                        path=Path("raw/video_1.mov"),
                        role="primary",
                        beep_time=5.0,
                        beep_source="auto",
                        beep_reviewed=True,
                        processed={"beep": True, "shot_detect": True, "trim": True},
                    )
                ],
            ),
            ShooterStageData(
                stage_number=2,
                time_seconds=12.0,
                videos=[StageVideo(path=Path("raw/video_2.mov"), role="primary")],
            ),
        ],
    )
    match.add_shooter(match_root, shooter)
    shooter_root = Match.shooter_root(match_root, shooter.slug)

    project = MatchProject.init(shooter_root, name=match.name)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Stage One",
            time_seconds=11.0,
            videos=[
                StageVideo(
                    path=Path("raw/video_1.mov"),
                    role="primary",
                    beep_time=5.0,
                    beep_source="auto",
                    beep_reviewed=True,
                    processed={"beep": True, "shot_detect": True, "trim": True},
                )
            ],
        ),
        StageEntry(
            stage_number=2,
            stage_name="Stage Two",
            time_seconds=12.0,
            videos=[StageVideo(path=Path("raw/video_2.mov"), role="primary")],
        ),
    ]
    project.save(shooter_root)

    exports = project.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"stage1_{_slugify('Stage One')}_trimmed.mp4").write_bytes(b"trim")

    return match_root


def test_loads_present_stages_and_skips_missing(tmp_path: Path) -> None:
    root = tmp_path / "shooter"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="Skipper",
                time_seconds=10.0,
                videos=[
                    StageVideo(path=Path("raw/v1.mp4"), role="primary", beep_time=12.5),
                ],
            ),
            StageEntry(
                stage_number=2,
                stage_name="No Trim Yet",
                time_seconds=10.0,
                videos=[
                    StageVideo(path=Path("raw/v2.mp4"), role="primary", beep_time=8.0),
                ],
            ),
        ],
    )

    # Stage 1's trim exists; stage 2's does not.
    trim1 = trim_path_for_stage(project, root, 1, "Skipper")
    _touch(trim1)

    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    assert set(bundle.stages_by_number) == {1}
    s1 = bundle.stages_by_number[1]
    assert s1.trim_path == trim1
    assert s1.audit_path == audit_path_for_stage(project, root, 1)
    # beep_time > pre_buffer so beep_offset_in_clip == pre_buffer
    assert s1.beep_offset_in_clip == 5.0


def test_short_head_clamps_beep_offset(tmp_path: Path) -> None:
    root = tmp_path / "short-head"
    project = _build_project(
        root,
        pre_buffer=5.0,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="Tight",
                time_seconds=10.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=2.5)],
            )
        ],
    )
    _touch(trim_path_for_stage(project, root, 1, "Tight"))

    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    # primary.beep_time < pre_buffer -> short head, clip-local beep equals beep_time
    assert bundle.stages_by_number[1].beep_offset_in_clip == 2.5


def test_skipped_stage_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "skipped"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="X",
                time_seconds=0.0,
                skipped=True,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=3.0)],
            )
        ],
    )
    _touch(trim_path_for_stage(project, root, 1, "X"))

    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    assert bundle.stages_by_number == {}


def test_no_primary_stage_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "noprim"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="X",
                time_seconds=0.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="secondary", beep_time=3.0)],
            )
        ],
    )
    _touch(trim_path_for_stage(project, root, 1, "X"))

    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    assert bundle.stages_by_number == {}


def test_no_beep_time_stage_is_omitted(tmp_path: Path) -> None:
    root = tmp_path / "nobeep"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="X",
                time_seconds=0.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary")],
            )
        ],
    )
    _touch(trim_path_for_stage(project, root, 1, "X"))

    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    assert bundle.stages_by_number == {}


def test_probe_metadata_propagates(tmp_path: Path) -> None:
    root = tmp_path / "meta"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="X",
                time_seconds=0.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=3.0)],
            )
        ],
    )
    trim = _touch(trim_path_for_stage(project, root, 1, "X"))

    custom = VideoMetadata(
        width=3840,
        height=2160,
        duration_seconds=42.0,
        frame_rate_num=60000,
        frame_rate_den=1001,
    )

    captured: list[Path] = []

    def probe(p: Path) -> VideoMetadata:
        captured.append(p)
        return custom

    bundle = load_shooter(root, "M", probe=probe)
    assert captured == [trim]
    s1 = bundle.stages_by_number[1]
    assert s1.duration_seconds == 42.0
    assert s1.width == 3840
    assert s1.height == 2160
    assert s1.frame_rate_num == 60000
    assert s1.frame_rate_den == 1001
    # convenience accessor reconstructs a VideoMetadata
    assert isinstance(s1.metadata, VideoMetadata)
    assert s1.metadata.frame_rate_num == 60000


def test_slug_drives_trim_filename(tmp_path: Path) -> None:
    """Stage with a complex name resolves through ``_slugify`` for the filename."""
    root = tmp_path / "slug"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=4,
                stage_name="Per told me to do it!",
                time_seconds=0.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=2.0)],
            )
        ],
    )
    expected = project.exports_path(root) / "stage4_per-told-me-to-do-it_trimmed.mp4"
    _touch(expected)
    bundle = load_shooter(root, "M", probe=lambda _p: _meta())
    assert bundle.stages_by_number[4].trim_path == expected


def test_default_probe_is_fcpxml_gen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When ``probe`` is omitted, :func:`fcpxml_gen.probe_video` is used."""
    root = tmp_path / "defaultprobe"
    project = _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="X",
                time_seconds=0.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=3.0)],
            )
        ],
    )
    _touch(trim_path_for_stage(project, root, 1, "X"))

    calls: list[Path] = []

    def fake_probe(p: Path) -> VideoMetadata:
        calls.append(p)
        return _meta()

    import splitsmith.fcpxml_gen as fg

    monkeypatch.setattr(fg, "probe_video", fake_probe)
    bundle = load_shooter(root, "M")  # no probe= -> module default
    assert len(calls) == 1
    assert bundle.stages_by_number[1].duration_seconds == 30.0


def test_load_shooter_from_match_sees_post_merge_beeps(tmp_path: Path) -> None:
    """Beeps detected after the merge live in project.json; shooter.json is
    a merge-time snapshot nothing updates. Reading the snapshot silently
    drops every stage the user beeped after merging."""
    match_root = _build_two_stage_match(tmp_path)
    shooter_root = Match.shooter_root(match_root, "mathias")

    # Simulate the server confirming a beep after the merge: project.json
    # is written, shooter.json is deliberately left stale.
    proj = MatchProject.load(shooter_root)
    proj.stage(2).primary().beep_time = 12.5
    proj.save(shooter_root)

    exports = shooter_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"stage2_{_slugify('Stage Two')}_trimmed.mp4").write_bytes(b"trim")

    bundle = load_shooter_from_match(match_root, "mathias", "Mathias", probe=_stub_probe)

    assert 2 in bundle.stages_by_number
    assert bundle.stages_by_number[2].beep_offset_in_clip == pytest.approx(5.0)
