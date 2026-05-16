"""Tests for the mutating MCP tools (issue #211 layer 3b)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith.config import BeepCandidate
from splitsmith.mcp import write_tools
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _build_project(
    root: Path,
    *,
    stages: list[StageEntry] | None = None,
) -> MatchProject:
    project = MatchProject.init(root, name="MCP Write Test")
    if stages is not None:
        project.stages = stages
    project.save(root)
    return project


def _stage_with_primary(
    *,
    stage_number: int = 1,
    primary_path: Path = Path("raw/p.mp4"),
    primary_kwargs: dict | None = None,
) -> StageEntry:
    """Helper: build a stage with one primary video."""
    primary = StageVideo(
        path=primary_path,
        role="primary",
        **(primary_kwargs or {}),
    )
    return StageEntry(
        stage_number=stage_number,
        stage_name=f"Stage {stage_number}",
        time_seconds=12.0,
        videos=[primary],
    )


# ---------------------------------------------------------------------------
# assign_video
# ---------------------------------------------------------------------------


def test_assign_video_moves_unassigned_video_to_stage(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _build_project(
        root,
        stages=[
            StageEntry(stage_number=1, stage_name="One", time_seconds=12.0, videos=[]),
        ],
    )
    project.unassigned_videos = [StageVideo(path=Path("raw/v.mp4"), role="secondary")]
    project.save(root)

    result = write_tools.assign_video(str(root), "raw/v.mp4", stage_number=1, role="secondary")
    # Reload to confirm persistence.
    after = MatchProject.load(root)
    assert len(after.unassigned_videos) == 0
    assert len(after.stages[0].videos) == 1
    # First-video-on-stage auto-upgrade: secondary -> primary.
    assert after.stages[0].videos[0].role == "primary"
    assert result["role"] == "primary"
    assert result["stage_number"] == 1


def test_assign_video_unassigns(tmp_path: Path) -> None:
    """``stage_number=None`` returns the video to the unassigned tray."""
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])

    result = write_tools.assign_video(str(root), "raw/p.mp4", stage_number=None, role="secondary")
    after = MatchProject.load(root)
    assert len(after.stages[0].videos) == 0
    assert len(after.unassigned_videos) == 1
    assert result["stage_number"] is None


def test_assign_video_rejects_unknown_role(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root)
    with pytest.raises(ValueError, match="role must be one of"):
        write_tools.assign_video(str(root), "raw/v.mp4", stage_number=1, role="bogus")


def test_assign_video_demotes_existing_primary(tmp_path: Path) -> None:
    """Promoting a second video to primary demotes the first."""
    root = tmp_path / "match"
    stage = StageEntry(
        stage_number=1,
        stage_name="Demote",
        time_seconds=12.0,
        videos=[
            StageVideo(path=Path("raw/old.mp4"), role="primary"),
            StageVideo(path=Path("raw/new.mp4"), role="secondary"),
        ],
    )
    _build_project(root, stages=[stage])
    write_tools.assign_video(str(root), "raw/new.mp4", stage_number=1, role="primary")
    after = MatchProject.load(root)
    roles = {str(v.path): v.role for v in after.stages[0].videos}
    assert roles["raw/new.mp4"] == "primary"
    assert roles["raw/old.mp4"] == "secondary"


# ---------------------------------------------------------------------------
# set_beep_manual
# ---------------------------------------------------------------------------


def test_set_beep_manual_pins_time_and_clamps_confidence(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim"):
        result = write_tools.set_beep_manual(str(root), stage_number=1, video_id=primary_id, time_seconds=4.5)
    after = MatchProject.load(root)
    primary = after.stages[0].videos[0]
    assert primary.beep_time == 4.5
    assert primary.beep_source == "manual"
    assert primary.beep_confidence == 1.0
    assert primary.beep_reviewed is True
    assert result["beep_confidence"] == 1.0


def test_set_beep_manual_clear_resets_state(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                primary_kwargs={
                    "beep_time": 5.0,
                    "beep_source": "auto",
                    "beep_confidence": 0.85,
                    "beep_reviewed": True,
                    "processed": {"beep": True, "shot_detect": False, "trim": True},
                }
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim"):
        write_tools.set_beep_manual(str(root), stage_number=1, video_id=primary_id, time_seconds=None)
    primary = MatchProject.load(root).stages[0].videos[0]
    assert primary.beep_time is None
    assert primary.beep_source is None
    assert primary.beep_confidence is None
    assert primary.beep_reviewed is False
    assert primary.processed["beep"] is False
    assert primary.processed["trim"] is False


def test_set_beep_manual_invalidates_audit_trim(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim") as mock_invalidate:
        write_tools.set_beep_manual(str(root), stage_number=1, video_id=primary_id, time_seconds=4.5)
    assert mock_invalidate.call_count == 1


def test_set_beep_manual_rejects_negative_time(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with pytest.raises(ValueError, match=">= 0"):
        write_tools.set_beep_manual(str(root), stage_number=1, video_id=primary_id, time_seconds=-0.5)


def test_set_beep_manual_unknown_stage_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root)
    with pytest.raises(ValueError, match="stage 99 not found"):
        write_tools.set_beep_manual(str(root), stage_number=99, video_id="abc", time_seconds=1.0)


def test_set_beep_manual_unknown_video_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    with pytest.raises(ValueError, match="not on stage"):
        write_tools.set_beep_manual(str(root), stage_number=1, video_id="missing", time_seconds=1.0)


# ---------------------------------------------------------------------------
# select_beep_candidate
# ---------------------------------------------------------------------------


def test_select_beep_candidate_promotes_match(tmp_path: Path) -> None:
    root = tmp_path / "match"
    candidates = [
        BeepCandidate(
            time=4.500,
            score=10.0,
            peak_amplitude=0.42,
            duration_ms=350.0,
            silence_score=8.0,
            tonal_score=0.95,
            confidence=0.88,
        ),
        BeepCandidate(
            time=7.250,
            score=5.0,
            peak_amplitude=0.30,
            duration_ms=180.0,
            silence_score=6.0,
            tonal_score=0.70,
            confidence=0.45,
        ),
    ]
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                primary_kwargs={
                    "beep_time": 4.5,
                    "beep_source": "auto",
                    "beep_candidates": candidates,
                    "beep_reviewed": True,
                    "beep_confidence": 0.88,
                }
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim"):
        result = write_tools.select_beep_candidate(
            str(root), stage_number=1, video_id=primary_id, time_seconds=7.250
        )
    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_time == pytest.approx(7.250)
    assert after.beep_source == "auto"
    assert after.beep_confidence == pytest.approx(0.45)
    # Switching candidate resets reviewed -- new claim needs new confirmation.
    assert after.beep_reviewed is False
    assert result["beep_reviewed"] is False


def test_select_beep_candidate_no_match_within_1ms_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    candidates = [
        BeepCandidate(
            time=4.500,
            score=10.0,
            peak_amplitude=0.42,
            duration_ms=350.0,
            silence_score=8.0,
            tonal_score=0.95,
            confidence=0.88,
        ),
    ]
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                primary_kwargs={
                    "beep_time": 4.5,
                    "beep_source": "auto",
                    "beep_candidates": candidates,
                }
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim"):
        with pytest.raises(ValueError, match="no candidate within"):
            write_tools.select_beep_candidate(
                str(root), stage_number=1, video_id=primary_id, time_seconds=99.0
            )


def test_select_beep_candidate_empty_list_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with patch("splitsmith.mcp.write_tools.audio_helpers.invalidate_video_audit_trim"):
        with pytest.raises(ValueError, match="no candidate list"):
            write_tools.select_beep_candidate(
                str(root), stage_number=1, video_id=primary_id, time_seconds=4.5
            )


# ---------------------------------------------------------------------------
# mark_beep_reviewed
# ---------------------------------------------------------------------------


def test_mark_beep_reviewed_flips_flag(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                primary_kwargs={
                    "beep_time": 4.5,
                    "beep_source": "auto",
                    "beep_reviewed": False,
                }
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    result = write_tools.mark_beep_reviewed(str(root), stage_number=1, video_id=primary_id, reviewed=True)
    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_reviewed is True
    assert result["beep_reviewed"] is True


def test_mark_beep_reviewed_rejects_when_no_beep_yet(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with pytest.raises(ValueError, match="before one has been detected"):
        write_tools.mark_beep_reviewed(str(root), stage_number=1, video_id=primary_id, reviewed=True)


def test_mark_beep_reviewed_unmark_allowed_without_beep(tmp_path: Path) -> None:
    """Setting reviewed=False is always allowed -- no precondition."""
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary()])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    write_tools.mark_beep_reviewed(str(root), stage_number=1, video_id=primary_id, reviewed=False)
    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_reviewed is False
