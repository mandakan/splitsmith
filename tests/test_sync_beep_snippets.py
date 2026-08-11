"""Tests for the desktop-side beep review snippet generator (slice 3, #631).

``generate_beep_snippets`` runs before a push: it cuts a short mono AAC
snippet plus a peaks JSON for every unconfirmed queue-worthy video into
``<shooter_root>/beep_review/``, so a phone can review the beep on a
mirror match. Reviewed videos get their snippet files removed so they
stop being pushed.

Cases:
  1. An unreviewed primary video with a beep_time gets a snippet cut
     around the beep, and the peaks JSON carries the expected shape.
  2. A second run with unchanged inputs skips the snippet (input_hash
     match); changing ``beep_time`` regenerates it.
  3. A video that becomes reviewed gets its stale snippet files removed
     and produces nothing new.

``_build_match_root`` here is a local, deliberately small stand-in for
``tests/test_sync_plan.py``'s ``_build_basic_match`` - that helper
returns a bare ``(root, slug)`` with no video assigned, which is not
enough for this module (it needs a real primary video with a beep). It
is not extracted from ``test_sync_plan.py`` and does not modify it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.sync.beep_snippets import generate_beep_snippets


def _make_source(path: Path, seconds: float = 20.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration={seconds}",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def _build_match_root(tmp_path: Path) -> tuple[Path, Path, MatchProject]:
    """Redesign-era match tree: one match, one shooter ("alice"), one
    stage with a primary video registered at a relative path. Returns
    (match_root, shooter_root, project)."""
    match_root = tmp_path / "match"
    match = match_model.Match.init(match_root, name="Test Match")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(match_root)

    slug = "alice"
    shooter = match_model.Shooter(slug=slug, name="Alice")
    match.add_shooter(match_root, shooter)
    shooter_root = match_model.Match.shooter_root(match_root, slug)

    project = MatchProject.init(shooter_root, name="Test Match")
    video = StageVideo(path=Path("raw/vid1.mp4"), role="primary")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0, videos=[video])]
    project.save(shooter_root)
    # Direct assignment to project.stages bypasses pydantic's
    # validate_assignment (off by default), so the in-memory video keeps
    # stage_number=None until something re-validates it - reload so the
    # video this helper hands back has the same stamped stage_number (and
    # therefore the same video_id) that generate_beep_snippets will see
    # when it loads the project fresh from disk.
    project = MatchProject.load(shooter_root)

    return match_root, shooter_root, project


def _seed(tmp_path: Path, *, beep_time: float = 6.0, reviewed: bool = False):
    """Match tree with one shooter, one stage, one primary video backed by
    a real 20 s sine source ffmpeg can cut."""
    match_root, shooter_root, project = _build_match_root(tmp_path)
    video = project.stages[0].videos[0]
    video.beep_time = beep_time
    video.beep_reviewed = reviewed
    project.save(shooter_root)
    src = shooter_root / str(video.path)
    src.parent.mkdir(parents=True, exist_ok=True)
    _make_source(src)
    return match_root, shooter_root, project, video


def test_generates_snippet_for_unreviewed_video(tmp_path: Path):
    match_root, shooter_root, _project, video = _seed(tmp_path)

    report = generate_beep_snippets(match_root)
    assert report.generated == 1 and not report.errors
    out = shooter_root / "beep_review"
    m4a = out / f"{video.video_id}.m4a"
    peaks = json.loads((out / f"{video.video_id}.peaks.json").read_text())
    assert m4a.stat().st_size > 0
    assert peaks["snippet_start"] == pytest.approx(1.0)  # 6.0 - 5.0 margin
    assert peaks["duration"] == pytest.approx(10.0, abs=1.0)  # margin both sides
    assert peaks["beep_time"] == 6.0
    assert len(peaks["peaks"]) == peaks["bins"]


def test_skips_when_inputs_unchanged_and_regenerates_on_change(tmp_path: Path):
    match_root, shooter_root, project, video = _seed(tmp_path)
    assert generate_beep_snippets(match_root).generated == 1

    second = generate_beep_snippets(match_root)
    assert second.generated == 0 and second.skipped == 1

    video.beep_time = 8.5
    project.save(shooter_root)
    third = generate_beep_snippets(match_root)
    assert third.generated == 1


def test_reviewed_video_gets_no_snippet_and_stale_one_is_removed(tmp_path: Path):
    match_root, shooter_root, project, video = _seed(tmp_path)
    assert generate_beep_snippets(match_root).generated == 1  # snippet exists

    video.beep_reviewed = True
    project.save(shooter_root)
    report = generate_beep_snippets(match_root)
    assert report.generated == 0 and report.removed == 1
    out = shooter_root / "beep_review"
    assert not (out / f"{video.video_id}.m4a").exists()
    assert not (out / f"{video.video_id}.peaks.json").exists()
