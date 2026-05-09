"""Tests for the per-shooter project loader in compare/project_loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.compare.project_loader import (
    audit_path_for_stage,
    load_shooter,
    trim_path_for_stage,
)
from splitsmith.fcpxml_gen import VideoMetadata
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
