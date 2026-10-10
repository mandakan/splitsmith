"""Tests for the detection MCP tools (issue #211 layer 3c)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from splitsmith.beep_detect import BeepNotFoundError
from splitsmith.config import BeepCandidate, BeepDetection
from splitsmith.match_project import (
    STUB_AUDIT_DETECTION,
    MatchProject,
    StageEntry,
    StageVideo,
    is_stub_audit,
)
from splitsmith.mcp import detect_tools


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
            vote_total=3,
            apriori_boost=0.0,
            ensemble_score=3.0,
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


def _fake_detection(*, time: float = 5.5, confidence: float = 0.9, candidates: int = 2) -> BeepDetection:
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
    fake = _fake_detection(confidence=0.98)

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake):
        result = detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    after = MatchProject.load(root).stages[0].videos[0]
    assert after.beep_reviewed is True
    assert result["beep_reviewed"] is True
    # Surfacing the threshold lets the agent explain to the user
    # WHY auto-trust opened (or didn't).
    assert result["auto_trust_threshold"] == 0.97


def test_detect_beep_records_which_ranker_chose_the_beep(tmp_path: Path) -> None:
    """Stored candidate scores mean a probability or a heuristic product
    depending on the ranker (#949); the video says which."""
    root = tmp_path / "match"
    _build_project(root, stages=[_stage_with_primary(root)])
    primary_id = MatchProject.load(root).stages[0].videos[0].video_id
    fake = _fake_detection(confidence=0.5).model_copy(update={"ranker_version": "beep-ranker-lr-test"})

    with patch("splitsmith.mcp.detect_tools.audio_helpers.detect_video_beep", return_value=fake):
        detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id)

    assert MatchProject.load(root).stages[0].videos[0].beep_ranker_version == "beep-ranker-lr-test"


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
        detect_tools.detect_beep_for_video(str(root), stage_number=1, video_id=primary_id, force=True)

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


def test_detect_shots_backfills_base_fields_over_a_beep_confirm_stub(tmp_path: Path) -> None:
    """Confirming a beep in the SPA seeds ``{"shots": [], "detection": "none"}``.

    Detecting via MCP afterwards used to fold shots into that stub verbatim,
    saving a document with no ``beep_time`` -- which the compare-timeline
    exporter keys on, so every shot silently vanished from the grid.
    """
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    audit_file.write_text(
        json.dumps({"shots": [], "detection": STUB_AUDIT_DETECTION}, indent=2), encoding="utf-8"
    )
    fake_result = _fake_ensemble_result([5.5, 6.1])

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
        detect_tools.detect_shots_for_stage(str(root), stage_number=1)

    payload = json.loads(audit_file.read_text())
    assert payload["beep_time"] == pytest.approx(5.0)
    assert payload["stage_number"] == 1
    assert payload["stage_name"] == "One"
    assert payload["stage_time_seconds"] == pytest.approx(12.0)
    assert len(payload["shots"]) == 2
    # The sentinel is dropped once real detection has run, and the doc can
    # no longer read back as a placeholder either way.
    assert "detection" not in payload
    assert is_stub_audit(payload) is False


def test_detect_shots_does_not_overwrite_an_existing_beep_time(tmp_path: Path) -> None:
    """Backfill is ``setdefault``: a doc that already carries a beep keeps it,
    even when the audit clip's beep-in-clip disagrees."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    audit_file.write_text(
        json.dumps({"stage_number": 1, "beep_time": 4.25, "shots": []}, indent=2), encoding="utf-8"
    )

    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav, beep_in_clip=5.0),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch(
            "splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble",
            return_value=_fake_ensemble_result([5.5]),
        ),
    ):
        detect_tools.detect_shots_for_stage(str(root), stage_number=1)

    payload = json.loads(audit_file.read_text())
    assert payload["beep_time"] == pytest.approx(4.25)
    assert payload["stage_name"] == "One"


# ---------------------------------------------------------------------------
# The server's reset rule, through the MCP tool (#1380)
# ---------------------------------------------------------------------------


def _detect_via_mcp(root: Path, wav: Path, result_or_effect: Any, *, reset: bool = False) -> dict:
    """Run the MCP tool with the heavy edges stubbed. ``result_or_effect`` is
    an ``EnsembleResult`` or a callable standing in for the ensemble."""
    detect_kw = (
        {"side_effect": result_or_effect}
        if callable(result_or_effect)
        else {"return_value": result_or_effect}
    )
    with (
        patch(
            "splitsmith.mcp.detect_tools.audio_helpers.ensure_audit_audio",
            return_value=_FakeAudit(wav, beep_in_clip=5.0),
        ),
        patch("splitsmith.mcp.detect_tools._get_ensemble_runtime", return_value=None),
        patch("splitsmith.mcp.detect_tools.ensemble_module.detect_shots_ensemble", **detect_kw),
    ):
        return detect_tools.detect_shots_for_stage(str(root), stage_number=1, reset=reset)


def test_detect_shots_reset_applies_the_servers_reset_rule(tmp_path: Path) -> None:
    """An MCP reset used to wipe ``shots[]`` and nothing else: the seeder's
    ``auto`` proposals and ``events_seeded`` survived (no reseed over the new
    shots), the wiped shots and dropped proposals went unlogged, and the new
    run numbered its candidates from 1 again, reusing ids the log still
    holds verdicts for."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    manual = {"id": "evt-2", "kind": "movement", "start": 0.2, "end": 0.9, "source": "manual"}
    audit_file.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "One",
                "stage_time_seconds": 12.0,
                "beep_time": 5.0,
                "shots": [
                    {"shot_number": 1, "id": "cand-4", "candidate_number": 4, "time": 5.5},
                    {"shot_number": 2, "id": "cand-9", "candidate_number": 9, "time": 6.5},
                ],
                "events": [
                    {"id": "evt-1", "kind": "reload", "start": 0.5, "end": 1.5, "source": "auto"},
                    manual,
                ],
                "events_seeded": 2,
                # Only the log still remembers candidate 12.
                "audit_events": [
                    {
                        "id": "e0",
                        "ts": "2026-08-12T12:00:00Z",
                        "kind": "marker_rejected",
                        "payload": {"id": "cand-12"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _detect_via_mcp(root, wav, _fake_ensemble_result([5.6, 6.2]), reset=True)

    doc = json.loads(audit_file.read_text())
    assert doc["events"] == [manual]
    assert "events_seeded" not in doc
    deleted = [e["payload"] for e in doc["audit_events"] if e["kind"] == "marker_deleted"]
    assert deleted == [
        {"id": "cand-4", "reason": "shot_detect_reset"},
        {"id": "cand-9", "reason": "shot_detect_reset"},
    ]
    reset_logs = [e for e in doc["audit_events"] if e["kind"] == "events_reset"]
    assert [e["payload"] for e in reset_logs] == [
        {"count": 1, "ids": ["evt-1"], "reason": "shot_detect_reset"}
    ]
    kinds = [e["kind"] for e in doc["audit_events"]]
    assert kinds.index("marker_deleted") < kinds.index("shot_detect_run")
    assert kinds.index("events_reset") < kinds.index("shot_detect_run")
    assert doc["audit_events"][-1]["payload"]["source"] == "mcp"
    # Numbering continues past the high-water mark (12, from the log).
    assert [s["candidate_number"] for s in doc["shots"]] == [13, 14]
    assert [c["candidate_number"] for c in doc["_candidates_pending_audit"]["candidates"]] == [13, 14]
    assert result["shots_seeded"] is True
    assert result["shot_count"] == 2


def test_detect_shots_without_reset_numbers_past_the_high_water_mark(tmp_path: Path) -> None:
    """The non-reset path shares the rule too: a re-detect over curated shots
    must not hand ``cand-<n>`` to a different candidate than the one it
    named, and it supersedes nothing, so it logs no deletes."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    audit_file.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "beep_time": 5.0,
                "shots": [{"shot_number": 1, "id": "cand-3", "candidate_number": 3, "time": 5.5}],
                "events": [{"id": "evt-1", "kind": "reload", "start": 0.5, "end": 1.5, "source": "auto"}],
                "events_seeded": 2,
            }
        ),
        encoding="utf-8",
    )

    _detect_via_mcp(root, wav, _fake_ensemble_result([5.6, 6.2]))

    doc = json.loads(audit_file.read_text())
    assert [c["candidate_number"] for c in doc["_candidates_pending_audit"]["candidates"]] == [4, 5]
    assert [s["candidate_number"] for s in doc["shots"]] == [3]
    assert doc["events_seeded"] == 2
    assert len(doc["events"]) == 1
    assert {e["kind"] for e in doc["audit_events"]} == {"shot_detect_run"}


def test_detect_shots_keeps_an_edit_saved_while_detection_ran(tmp_path: Path) -> None:
    """The tool merges onto the document as it stands after detection, not
    the one it read before: shots curated in the SPA during the run (tens of
    seconds on CPU) used to be overwritten by a fresh seed."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_file = root / "audit" / "stage1.json"
    curated = [{"shot_number": 1, "time": 5.42, "source": "manual"}]
    fake_result = _fake_ensemble_result([5.5, 6.1, 6.9])

    def _detect_while_the_user_edits(*_a: Any, **_kw: Any) -> Any:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text(json.dumps({"stage_number": 1, "shots": curated}), encoding="utf-8")
        return fake_result

    result = _detect_via_mcp(root, wav, _detect_while_the_user_edits)

    doc = json.loads(audit_file.read_text())
    assert doc["shots"] == curated
    assert result["shots_seeded"] is False


def test_detect_shots_keeps_a_project_edit_saved_while_detection_ran(tmp_path: Path) -> None:
    """Flagging ``shot_detect`` re-loads the project, as the server's job
    does: saving the snapshot read before detection used to undo whatever
    the SPA wrote to ``project.json`` during the run."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    fake_result = _fake_ensemble_result([5.5, 6.1])

    def _detect_while_the_user_edits(*_a: Any, **_kw: Any) -> Any:
        project = MatchProject.load(root)
        project.competitor_division = "Production Optics"
        project.stages[0].videos[0].processed["trim"] = True
        project.save(root)
        return fake_result

    _detect_via_mcp(root, wav, _detect_while_the_user_edits)

    project = MatchProject.load(root)
    assert project.competitor_division == "Production Optics"
    primary = project.stages[0].videos[0]
    assert primary.processed["trim"] is True
    assert primary.processed["shot_detect"] is True


def test_detect_shots_logs_an_audit_doc_that_is_not_an_object(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A doc that parses but is not a JSON object is discarded out loud, like
    an unparseable one, and the previous file survives as the ``.bak``."""
    root, _src, wav = _seed_shot_detect_project(tmp_path)
    audit_file = root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text("[1, 2]", encoding="utf-8")

    with caplog.at_level("WARNING", logger="splitsmith.mcp.detect_tools"):
        _detect_via_mcp(root, wav, _fake_ensemble_result([5.5]))

    assert any(
        "expected an object, found list" in r.getMessage() and str(audit_file) in r.getMessage()
        for r in caplog.records
    )
    assert len(json.loads(audit_file.read_text())["shots"]) == 1
    assert (audit_file.parent / "stage1.json.bak").read_text(encoding="utf-8") == "[1, 2]"


def test_coach_get_reseeds_after_an_mcp_reset(tmp_path: Path) -> None:
    """End to end: after an MCP reset the next coach GET seeds a reload
    proposal over the *new* shots' gap. Before the fix ``events_seeded``
    survived the reset, so the stale proposal stayed and nothing reseeded."""
    import numpy as np
    import soundfile as sf
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app
    from tests.conftest import scaffold_match

    match_root, shooter_root = scaffold_match(tmp_path, name="MCP Reseed")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "v.mp4").write_bytes(b"FAKE_MP4")
    wav = shooter_root / "audio" / "stage1_audit.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), np.zeros(48_000, dtype="float32"), 48_000)
    project = MatchProject.load(shooter_root)
    project.competitor_division = "Production Optics"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(shooter_root)
    audit_file = shooter_root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    # The previous run's shots, with the seeder's proposal over their gap.
    audit_file.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "beep_time": 5.0,
                "shots": [{"shot_number": 1, "time": 6.0, "ms_after_beep": 1000, "source": "detected"}],
                "events": [{"id": "evt-1", "kind": "reload", "start": 9.0, "end": 10.0, "source": "auto"}],
                "events_seeded": 2,
            }
        ),
        encoding="utf-8",
    )

    # Twelve quick shots, a 3.2 s reload gap, eight more (a PO magazine).
    quick = [1.2 + i * 0.3 for i in range(12)]
    after = [quick[-1] + 3.2 + i * 0.3 for i in range(8)]
    _detect_via_mcp(shooter_root, wav, _fake_ensemble_result([5.0 + t for t in quick + after]), reset=True)

    app = create_app(project_root=match_root, project_name="MCP Reseed")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    body = TestClient(app).get(f"/api/matches/{match_id}/shooters/me/stages/1/coach").json()
    assert [(e["kind"], e["source"]) for e in body["events"]] == [("reload", "auto")]
    assert body["events"][0]["start"] == pytest.approx(quick[-1], abs=0.01)


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


# ---------------------------------------------------------------------------
# trim_audit_clip
# ---------------------------------------------------------------------------


def _seed_trim_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Project ready for trim: primary with beep_time + stage time + source."""
    root = tmp_path / "match"
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"FAKE_MP4")
    primary = StageVideo(
        path=Path("raw/primary.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    project = MatchProject.init(root, name="MCP Trim Test")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    out = root / "trimmed" / "stage1_trimmed.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return root, src, out


def test_trim_audit_clip_calls_helper_and_flips_flag(tmp_path: Path) -> None:
    root, _src, out_path = _seed_trim_project(tmp_path)

    with patch(
        "splitsmith.mcp.detect_tools.audio_helpers.ensure_video_audit_trim",
        return_value=out_path,
    ) as mock_trim:
        result = detect_tools.trim_audit_clip(str(root), stage_number=1)

    assert mock_trim.call_count == 1
    # Helper should be called with the primary's beep_time + stage time.
    kwargs = mock_trim.call_args
    args = kwargs.args
    # ensure_video_audit_trim signature: (root, stage_number, video, source,
    # beep_time, stage_time_seconds, *, project=...)
    assert args[1] == 1  # stage_number
    assert args[4] == 5.0  # beep_time
    assert args[5] == 12.0  # stage_time
    after = MatchProject.load(root).stages[0].videos[0]
    assert after.processed["trim"] is True
    assert result["output_path"].endswith("stage1_trimmed.mp4")
    assert result["beep_time"] == 5.0


def test_trim_audit_clip_targets_secondary_when_video_id_given(
    tmp_path: Path,
) -> None:
    root = tmp_path / "match"
    primary_src = root / "raw" / "p.mp4"
    secondary_src = root / "raw" / "s.mp4"
    for s in (primary_src, secondary_src):
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_bytes(b"x")
    primary = StageVideo(
        path=Path("raw/p.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
        beep_confidence=1.0,
    )
    secondary = StageVideo(
        path=Path("raw/s.mp4"),
        role="secondary",
        beep_time=4.5,
        beep_source="aligned",
    )
    project = MatchProject.init(root, name="Trim Sec")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary, secondary],
        )
    ]
    project.save(root)
    secondary_id = MatchProject.load(root).stages[0].videos[1].video_id

    out = root / "trimmed" / f"stage1_cam_{secondary_id[:6]}_trimmed.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    with patch(
        "splitsmith.mcp.detect_tools.audio_helpers.ensure_video_audit_trim",
        return_value=out,
    ) as mock_trim:
        result = detect_tools.trim_audit_clip(str(root), stage_number=1, video_id=secondary_id)

    # Helper got the secondary, not the primary.
    args = mock_trim.call_args.args
    assert args[2].video_id == secondary_id
    assert args[4] == 4.5  # secondary's own beep_time
    after = MatchProject.load(root).stages[0]
    sec_after = next(v for v in after.videos if v.video_id == secondary_id)
    assert sec_after.processed["trim"] is True
    # Primary's flag is untouched -- targeting one video doesn't ripple.
    prim_after = next(v for v in after.videos if v.role == "primary")
    assert prim_after.processed["trim"] is False
    assert result["video_id"] == secondary_id


def test_trim_audit_clip_rejects_video_without_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "p.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    primary = StageVideo(path=Path("raw/p.mp4"), role="primary")
    project = MatchProject.init(root, name="No Beep Trim")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="no beep_time"):
        detect_tools.trim_audit_clip(str(root), stage_number=1)


def test_trim_audit_clip_rejects_zero_stage_time(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "p.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    primary = StageVideo(
        path=Path("raw/p.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
    )
    project = MatchProject.init(root, name="No Time Trim")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=0.0,
            videos=[primary],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="time_seconds=0"):
        detect_tools.trim_audit_clip(str(root), stage_number=1)


def test_trim_audit_clip_rejects_unknown_video_id(tmp_path: Path) -> None:
    root, _src, _out = _seed_trim_project(tmp_path)
    with pytest.raises(ValueError, match="not on stage"):
        detect_tools.trim_audit_clip(str(root), stage_number=1, video_id="bogus")


def test_trim_audit_clip_missing_source_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    primary = StageVideo(
        path=Path("raw/missing.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
    )
    project = MatchProject.init(root, name="Missing Source Trim")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    with pytest.raises(FileNotFoundError, match="source video missing"):
        detect_tools.trim_audit_clip(str(root), stage_number=1)
