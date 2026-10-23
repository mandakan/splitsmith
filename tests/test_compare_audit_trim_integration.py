"""The compare grid cut from audit trims, through the real ffmpeg (#1681).

The unit tests spell the trim job's ``.params.json`` out by hand. What only
this test can catch is the writer and the reader disagreeing: the trim job
(``ui.audio.ensure_video_audit_trim``) cuts real audit trims and writes their
sidecars, nothing is exported, and the compare loader must still find the
stages current and the grid must render from them, saying so.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import load_shooter_from_match
from splitsmith.match_model import Match, MatchStageDefinition, Shooter
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio as audio_helpers

BEEP_TIME = 6.0
STAGE_TIME = 4.0
SHOOTERS = ("anna", "mathias")


def _audit_trimmed_match(tmp_path: Path, source: Path) -> Path:
    """One stage, two shooters, each with the stage audit-trimmed by the
    trim job's own function and nothing exported."""
    match_root = tmp_path / "match"
    match = Match.init(match_root, name="Audit Only")
    match.stages = [MatchStageDefinition(stage_number=1, stage_name="Stage One")]
    match.save(match_root)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    for slug in SHOOTERS:
        match.add_shooter(match_root, Shooter(slug=slug, name=slug.capitalize()))
        shooter_root = Match.shooter_root(match_root, slug)
        raw = shooter_root / "raw" / "source.mp4"
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, raw)
        project = MatchProject.init(shooter_root, name=match.name)
        project.trim_pre_buffer_seconds = 2.0
        project.trim_post_buffer_seconds = 1.0
        project.stages = [
            StageEntry(
                stage_number=1,
                stage_name="Stage One",
                time_seconds=STAGE_TIME,
                videos=[StageVideo(path=Path("raw/source.mp4"), role="primary", beep_time=BEEP_TIME)],
            )
        ]
        project.save(shooter_root)
        project = MatchProject.load(shooter_root)
        video = project.stage(1).primary()
        assert video is not None
        audio_helpers.ensure_video_audit_trim(
            shooter_root, 1, video, raw, BEEP_TIME, STAGE_TIME, project=project, ffmpeg_binary=ffmpeg
        )
        assert not any(project.exports_path(shooter_root).glob("*_trimmed.mp4"))
    return match_root


@pytest.mark.integration
def test_the_grid_renders_from_the_trim_jobs_own_audit_trims(
    tmp_path: Path, synthetic_source_video: Path
) -> None:
    match_root = _audit_trimmed_match(tmp_path, synthetic_source_video)

    bundles = [load_shooter_from_match(match_root, slug, slug.capitalize()) for slug in SHOOTERS]

    for bundle in bundles:
        stage = bundle.stages_by_number[1]
        assert stage.trim_path.parent.name == "trimmed"
        assert stage.trim_source == "audit"
        assert stage.beep_offset_in_clip == pytest.approx(2.0)
        # pre 2.0 + stage 4.0 + post 1.0, probed off the real file.
        assert stage.duration_seconds == pytest.approx(7.0, abs=0.1)

    result = mp4_grid.render_grid_mp4(
        bundles,
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(width=320, height=180, frame_rate_num=30, frame_rate_den=1),
        work_dir=tmp_path / "work",
        ffmpeg_binary=shutil.which("ffmpeg"),
    )

    assert result.failed == ()
    assert result.output_path.stat().st_size > 0
    (degradation,) = result.degradations
    assert "Anna stage 1" in degradation.summary and "Mathias stage 1" in degradation.summary

    # Moving the beep makes the same file stale: the stage is missing again.
    shooter_root = Match.shooter_root(match_root, "mathias")
    project = MatchProject.load(shooter_root)
    project.stage(1).videos[0].beep_time = BEEP_TIME + 0.5
    project.save(shooter_root)
    stale = load_shooter_from_match(match_root, "mathias", "Mathias")
    assert 1 not in stale.stages_by_number
    assert [m.stage_number for m in stale.missing_trims] == [1]
