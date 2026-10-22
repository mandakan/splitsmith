"""Reference footage for the sort's compare panel
(``ui/footage_sort_reference.py``, spec 2026-10-21): which file stands for
a shooter's stage, and the frame that represents it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from splitsmith.export_naming import stage_file_base
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio
from splitsmith.ui import footage_sort_reference as reference


def _project(tmp_path: Path, *, beep_time: float | None = 7.0, raw: bool = True) -> tuple[MatchProject, Path]:
    root = tmp_path / "alice"
    project = MatchProject.init(root, name="Ref Test")
    project.trim_pre_buffer_seconds = 5.0
    source = tmp_path / "raw" / "take.mp4"
    if raw:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"\0" * 16)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Lucky Seven",
            time_seconds=20.0,
            videos=[StageVideo(path=source, role="primary", beep_time=beep_time)],
        )
    ]
    project.save(root)
    return project, root


def _audit_trim(project: MatchProject, root: Path) -> Path:
    return audio.trimmed_video_path(root, 1, project.stage(1).primary(), project=project)  # type: ignore[arg-type]


def test_stage_trim_for_read_prefers_the_export_trim(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    export = project.exports_path(root) / f"{stage_file_base(1, 'Lucky Seven')}_trimmed.mp4"
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_bytes(b"x")
    audit_trim = _audit_trim(project, root)
    audit_trim.parent.mkdir(parents=True, exist_ok=True)
    audit_trim.write_bytes(b"x")

    assert audio.stage_trim_for_read(project, root, 1) == (export, 5.0)


def test_stage_trim_for_read_falls_back_to_the_audit_trim(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audit_trim = _audit_trim(project, root)
    audit_trim.parent.mkdir(parents=True, exist_ok=True)
    audit_trim.write_bytes(b"x")

    assert audio.stage_trim_for_read(project, root, 1) == (audit_trim, 5.0)


def test_stage_trim_for_read_without_a_file_keeps_the_beep(tmp_path: Path) -> None:
    project, root = _project(tmp_path)

    assert audio.stage_trim_for_read(project, root, 1) == (None, 5.0)


def test_stage_trim_for_read_without_a_beep_is_nothing(tmp_path: Path) -> None:
    project, root = _project(tmp_path, beep_time=None)

    assert audio.stage_trim_for_read(project, root, 1) == (None, 0.0)


# --- the reference module ---------------------------------------------------


@pytest.fixture
def source_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return build_synthetic_video(tmp_path_factory.mktemp("media") / "source.mp4")


def test_project_reference_with_a_trim_is_trim_kind(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audit_trim = _audit_trim(project, root)
    audit_trim.parent.mkdir(parents=True, exist_ok=True)
    audit_trim.write_bytes(b"x")

    ref = reference.project_reference(project, root, "alice", 1)

    assert ref is not None
    assert (ref.stream_kind, ref.source_file, ref.frame_at) == ("trim", audit_trim, 7.0)
    assert ref.video_id == project.stage(1).primary().video_id  # type: ignore[union-attr]
    assert ref.video_path == str(project.stage(1).primary().path)  # type: ignore[union-attr]


def test_project_reference_without_a_trim_plays_the_raw_file(tmp_path: Path) -> None:
    project, root = _project(tmp_path)

    ref = reference.project_reference(project, root, "alice", 1)

    assert ref is not None
    assert (ref.stream_kind, ref.source_file, ref.frame_at) == ("auto", tmp_path / "raw" / "take.mp4", 1.0)


def test_unreachable_raw_file_is_no_reference(tmp_path: Path) -> None:
    project, root = _project(tmp_path, raw=False)

    assert reference.project_reference(project, root, "alice", 1) is None
    assert reference.project_reference(project, root, "alice", 9) is None


def test_trim_resolution_uses_the_read_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, root = _project(tmp_path)
    legacy = root / "trimmed" / "stage1_cam_legacy_trimmed.mp4"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"x")
    monkeypatch.setattr(audio, "resolve_trim_for_read", lambda *a, **k: legacy)

    ref = reference.project_reference(project, root, "alice", 1)

    assert ref is not None and ref.source_file == legacy and ref.stream_kind == "trim"


def test_frame_path_changes_with_the_source_mtime(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    ref = reference.project_reference(project, root, "alice", 1)
    assert ref is not None
    cache = tmp_path / "reference"

    before = reference.frame_path(cache, ref)
    os.utime(ref.source_file, (1_700_000_000, 1_700_000_000))
    after = reference.frame_path(cache, ref)

    assert before != after
    assert after.parent == cache and after.name.startswith("alice-stage01-")
    assert reference.cached_frame(cache, ref) is None


@pytest.mark.integration
def test_extract_frame_falls_back_to_the_start(tmp_path: Path, source_clip: Path) -> None:
    dest = tmp_path / "frame.jpg"

    reference.extract_frame(source_clip, dest, at_seconds=10_000)

    assert dest.exists() and dest.stat().st_size > 0


@pytest.mark.integration
def test_ensure_frame_builds_once(tmp_path: Path, source_clip: Path) -> None:
    project, root = _project(tmp_path, beep_time=None)
    project.stage(1).videos[0].path = source_clip
    ref = reference.project_reference(project, root, "alice", 1)
    assert ref is not None
    cache = tmp_path / "reference"

    made = reference.ensure_frame(cache, ref)
    again = reference.ensure_frame(cache, ref)

    assert made == again == reference.cached_frame(cache, ref)
    assert made.stat().st_size > 0


def test_an_export_trim_frames_but_streams_auto(tmp_path: Path) -> None:
    """The stream route's ``trim`` serves the audit trim only, so a stage
    with just the lossless export trim takes its frame from it and plays
    the source."""
    project, root = _project(tmp_path)
    export = project.exports_path(root) / f"{stage_file_base(1, 'Lucky Seven')}_trimmed.mp4"
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_bytes(b"x")

    ref = reference.project_reference(project, root, "alice", 1)

    assert ref is not None
    assert (ref.stream_kind, ref.source_file, ref.frame_at) == ("auto", export, 7.0)


def test_a_failed_frame_is_marked_until_the_source_changes(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    ref = reference.project_reference(project, root, "alice", 1)
    assert ref is not None
    cache = tmp_path / "reference"

    assert reference.frame_failed(cache, ref) is False
    reference.mark_failed(cache, ref)
    assert reference.frame_failed(cache, ref) is True
    os.utime(ref.source_file, (1_700_000_000, 1_700_000_000))
    assert reference.frame_failed(cache, ref) is False
