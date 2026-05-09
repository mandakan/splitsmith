"""Tests for the detection MCP tools (issue #211 layer 3c)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith.beep_detect import BeepNotFoundError
from splitsmith.config import BeepCandidate, BeepDetection
from splitsmith.mcp import detect_tools
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _build_project(
    root: Path,
    *,
    stages: list[StageEntry] | None = None,
) -> MatchProject:
    project = MatchProject.init(root, name="MCP Detect Test")
    if stages is not None:
        project.stages = stages
    project.save(root)
    return project


def _stage_with_primary(
    root: Path,
    *,
    primary_kwargs: dict | None = None,
) -> StageEntry:
    """Stage with a primary video whose source path actually exists on
    disk (the detector resolves + checks existence)."""
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"FAKE_MP4")
    primary = StageVideo(
        path=Path("raw/primary.mp4"),
        role="primary",
        **(primary_kwargs or {}),
    )
    return StageEntry(
        stage_number=1,
        stage_name="Stage 1",
        time_seconds=12.0,
        videos=[primary],
    )


def _fake_detection(
    *, time: float = 5.5, confidence: float = 0.9, candidates: int = 2
) -> BeepDetection:
    cands = [
        BeepCandidate(
            time=time + 0.5 * i,
            score=10.0 - i,
            peak_amplitude=0.4,
            duration_ms=350.0,
            silence_score=8.0,
            tonal_score=0.95,
            confidence=confidence - 0.1 * i,
        )
        for i in range(candidates)
    ]
    return BeepDetection(
        time=time,
        peak_amplitude=cands[0].peak_amplitude,
        duration_ms=cands[0].duration_ms,
        confidence=cands[0].confidence,
        candidates=cands,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_detect_beep_persists_result_on_project(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary(root)])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    fake = _fake_detection(time=4.875, confidence=0.92)

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake):
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_time == pytest.approx(4.875)
    assert after.beep_source == "auto"
    assert after.beep_confidence == pytest.approx(0.92)
    assert len(after.beep_candidates) == 2
    assert after.beep_auto_detect_failed is False
    assert result["beep_time"] == pytest.approx(4.875)
    assert result["candidate_count"] == 2
    assert result["error"] is None


def test_detect_beep_high_confidence_auto_trusts_into_reviewed(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary(root)])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    fake = _fake_detection(confidence=0.92)

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake):
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_reviewed is True
    assert result["beep_reviewed"] is True
    # Surfacing the threshold lets the agent explain to the user
    # WHY auto-trust opened (or didn't).
    assert result["auto_trust_threshold"] == 0.6


def test_detect_beep_low_confidence_lands_in_hitl(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary(root)])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    fake = _fake_detection(confidence=0.45)

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake):
        detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_reviewed is False
    assert after.beep_confidence == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# Detector failure
# ---------------------------------------------------------------------------


def test_detect_beep_marks_auto_detect_failed_on_no_candidate(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary(root)])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch(
        "splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep",
        side_effect=BeepNotFoundError("no candidate"),
    ):
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_time is None
    assert after.beep_auto_detect_failed is True
    assert after.processed["beep"] is True  # detection ran; just no candidate
    assert result["error"] == "not_found"
    assert result["beep_auto_detect_failed"] is True


# ---------------------------------------------------------------------------
# Skip / force semantics
# ---------------------------------------------------------------------------


def test_detect_beep_skips_when_already_detected(tmp_path: Path) -> None:
    """Default behaviour: re-detecting an already-detected video is a
    no-op so a workflow loop ``for stage in stages: detect_beep`` is
    idempotent."""
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                root,
                primary_kwargs={
                    "beep_time": 5.0,
                    "beep_source": "auto",
                    "beep_confidence": 0.85,
                    "processed": {"beep": True, "shot_detect": False, "trim": False},
                },
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep") as mock_detect:
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    mock_detect.assert_not_called()
    assert result["beep_time"] == 5.0


def test_detect_beep_force_reruns(tmp_path: Path) -> None:
    """``force=True`` runs the detector even when the video has a beep."""
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                root,
                primary_kwargs={
                    "beep_time": 5.0,
                    "beep_source": "auto",
                    "beep_confidence": 0.55,
                    "processed": {"beep": True, "shot_detect": False, "trim": False},
                },
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    fake = _fake_detection(time=4.5, confidence=0.92)

    with patch(
        "splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake
    ) as mock_detect:
        detect_tools.detect_beep_for_video(
            str(root), stage_number=1, video_id=primary_id, force=True
        )

    mock_detect.assert_called_once()
    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_time == pytest.approx(4.5)
    assert after.beep_confidence == pytest.approx(0.92)


def test_detect_beep_never_overwrites_manual_entry_without_force(tmp_path: Path) -> None:
    """Manual beep is the user's explicit intent. Re-detection without
    force is a no-op even though processed['beep'] is True."""
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            _stage_with_primary(
                root,
                primary_kwargs={
                    "beep_time": 5.0,
                    "beep_source": "manual",
                    "beep_confidence": 1.0,
                    "beep_reviewed": True,
                    "processed": {"beep": True, "shot_detect": False, "trim": False},
                },
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep") as mock_detect:
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    mock_detect.assert_not_called()
    assert result["beep_source"] == "manual"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_detect_beep_rejects_ignored_role(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "x.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    stage = StageEntry(
        stage_number=1,
        stage_name="Ignored",
        time_seconds=12.0,
        videos=[
            StageVideo(path=Path("raw/x.mp4"), role="ignored"),
        ],
    )
    _build_project(root, stages=[stage])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with pytest.raises(ValueError, match="ignored"):
        detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)


def test_detect_beep_missing_source_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    primary = StageVideo(path=Path("raw/missing.mp4"), role="primary")
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="Missing",
                time_seconds=12.0,
                videos=[primary],
            )
        ],
    )
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    with pytest.raises(FileNotFoundError, match="source video missing"):
        detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)
