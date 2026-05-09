"""Tests for the detection MCP tools (issue #211 layer 3c)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith.beep_detect import BeepNotFoundError
from splitsmith.config import BeepCandidate, BeepDetection
from splitsmith.mcp import detect_tools
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _fake_ensemble_result(times: list[float], consensus: int = 3, expected_rounds=None):
    """Build a small ``EnsembleResult`` for tests so we don't pull
    CLAP / GBDT / PANN weights into CI.

    Mirrors the helper in ``test_ui_server.py``; duplicated here so
    these tests stay self-contained."""
    from splitsmith.ensemble import EnsembleCandidate, EnsembleResult

    cands = [
        EnsembleCandidate(
            candidate_number=i + 1,
            time=t,
            ms_after_beep=round((t - 5.0) * 1000),
            peak_amplitude=0.5,
            confidence=0.85,
            vote_a=1,
            vote_b=1,
            vote_c=1,
            vote_d=1,
            vote_total=4,
            apriori_boost=0.0,
            ensemble_score=4.0,
            score_c=0.9,
            clap_diff=0.5,
            gunshot_prob=0.7,
            kept=True,
        )
        for i, t in enumerate(times)
    ]
    return EnsembleResult(candidates=cands, consensus=consensus, expected_rounds=expected_rounds)


class _FakeAudit:
    """Stub matching :class:`AuditAudioResult` -- audio_path + beep_in_clip."""

    def __init__(self, audio_path: Path, beep_in_clip: float = 5.0) -> None:
        self.audio_path = audio_path
        self.beep_in_clip = beep_in_clip
        self.trimmed = True


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


# ---------------------------------------------------------------------------
# detect_shots
# ---------------------------------------------------------------------------


def _seed_shot_detect_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a project ready for shot detection: primary with beep_time,
    stage time_seconds > 0, source video on disk, audit WAV synthesised so
    ``ensure_audit_audio`` can be stubbed cleanly. Returns (root, source, wav)."""
    import numpy as np
    import soundfile as sf

    root = tmp_path / "match"
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"FAKE_MP4")
    wav = root / "audio" / "stage1_audit.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), np.zeros(48_000, dtype="float32"), 48_000)

    primary = StageVideo(
        path=Path("raw/primary.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    project = MatchProject.init(root, name="MCP Shots Test")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    return root, src, wav


def test_detect_shots_writes_audit_json_and_seeds_shots(tmp_path: Path) -> None:
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    fake_result = _fake_ensemble_result([5.5, 6.1, 6.9])

    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav, beep_in_clip=5.0),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch(
            "splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble",
            return_value=fake_result,
        ),
    ):
        result = detect_tools.detect_shots_for_stage(str(root), stage_number=1)

    audit_file = root / "audit" / "stage1.json"
    assert audit_file.exists()
    payload = json.loads(audit_file.read_text())
    assert len(payload["shots"]) == 3
    # Source provenance lets the user filter ``audit_events`` by where a run came from.
    last_event = payload["audit_events"][-1]
    assert last_event["payload"]["source"] == "mcp"
    assert result["candidate_count"] == 3
    assert result["kept_count"] == 3
    assert result["shots_seeded"] is True
    # Project flag is flipped so the SPA's stage badge updates.
    primary = MatchProject.load(root).stages[0].videos[0]
    assert primary.processed["shot_detect"] is True


def test_detect_shots_preserves_curated_shots_by_default(tmp_path: Path) -> None:
    """When ``shots[]`` is already populated (user curated), default
    behaviour must not overwrite it -- the detector run still records
    candidates into ``_candidates_pending_audit`` for the audit UI."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    curated = {
        "stage_number": 1,
        "stage_name": "One",
        "stage_time_seconds": 12.0,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "time": 5.42, "source": "manual"},
            {"shot_number": 2, "time": 5.91, "source": "manual"},
        ],
    }
    audit_file.write_text(json.dumps(curated, indent=2))
    fake_result = _fake_ensemble_result([5.5, 6.1, 6.9])

    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch(
            "splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble",
            return_value=fake_result,
        ),
    ):
        result = detect_tools.detect_shots_for_stage(str(root), stage_number=1)

    payload = json.loads(audit_file.read_text())
    assert len(payload["shots"]) == 2  # curated list preserved
    assert payload["_candidates_pending_audit"]["candidates"]
    assert result["shots_seeded"] is False


def test_detect_shots_reset_wipes_existing_shots(tmp_path: Path) -> None:
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "One",
                "stage_time_seconds": 12.0,
                "beep_time": 5.0,
                "shots": [{"shot_number": 1, "time": 5.42, "source": "manual"}],
            }
        )
    )
    fake_result = _fake_ensemble_result([5.5, 6.1])

    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch(
            "splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble",
            return_value=fake_result,
        ),
    ):
        result = detect_tools.detect_shots_for_stage(str(root), stage_number=1, reset=True)

    payload = json.loads((root / "audit" / "stage1.json").read_text())
    # Reset cleared the manual shot, then seeded the 2 detected ones.
    assert len(payload["shots"]) == 2
    assert all(s["source"] == "detected" for s in payload["shots"])
    assert result["shots_seeded"] is True


def test_detect_shots_rejects_stage_without_beep_time(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    project = MatchProject.init(root, name="No Beep")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[StageVideo(path=Path("raw/primary.mp4"), role="primary")],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="no beep_time"):
        detect_tools.detect_shots_for_stage(str(root), stage_number=1)


def test_detect_shots_rejects_stage_without_time_seconds(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    project = MatchProject.init(root, name="No Time")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=0.0,
            videos=[
                StageVideo(
                    path=Path("raw/primary.mp4"),
                    role="primary",
                    beep_time=5.0,
                    beep_source="manual",
                    beep_confidence=1.0,
                )
            ],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="time_seconds"):
        detect_tools.detect_shots_for_stage(str(root), stage_number=1)


def test_detect_shots_rejects_stage_without_primary(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="No Primary")
    project.stages = [StageEntry(stage_number=1, stage_name="One", time_seconds=12.0, videos=[])]
    project.save(root)
    with pytest.raises(ValueError, match="no primary"):
        detect_tools.detect_shots_for_stage(str(root), stage_number=1)


def test_detect_shots_passes_expected_rounds_through_to_ensemble(tmp_path: Path) -> None:
    """When the audit JSON carries ``stage_rounds.expected``, the
    ensemble's adaptive voter C + apriori boost expects to receive it."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "One",
                "stage_time_seconds": 12.0,
                "beep_time": 5.0,
                "shots": [],
                "stage_rounds": {"expected": 7},
            }
        )
    )
    fake_result = _fake_ensemble_result([5.5], expected_rounds=7)

    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch(
            "splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble",
            return_value=fake_result,
        ) as mock_detect,
    ):
        result = detect_tools.detect_shots_for_stage(str(root), stage_number=1)

    # Verify the kwarg passthrough -- a regression that drops it would
    # silently turn voter C off the adaptive path.
    assert mock_detect.call_args.kwargs["expected_rounds"] == 7
    assert result["expected_rounds"] == 7
