"""Reference footage for the sort's compare panel
(``ui/footage_sort_reference.py``, spec 2026-10-21): which file stands for
a shooter's stage, and the frame that represents it."""

from __future__ import annotations

from pathlib import Path

from splitsmith.export_naming import stage_file_base
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio


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
