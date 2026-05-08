"""Unit tests for the beep calibration helpers (issue #220 layer 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.beep_calibration import (
    DEFAULT_TOLERANCE_MS,
    BeepCalibrationManifest,
    BeepFixtureEntry,
    auto_tags,
    compute_full_beep_time,
    derive_camera_kind,
    evaluate_detection,
    fixtures_with_full_audio,
    load_manifest,
    save_manifest,
    summarize,
)


def test_derive_camera_kind_handles_missing_block() -> None:
    assert derive_camera_kind(None) == "unknown"
    assert derive_camera_kind({}) == "unknown"
    assert derive_camera_kind({"mount": "head"}) == "head"
    assert derive_camera_kind({"mount": "hand"}) == "hand"
    assert derive_camera_kind({"mount": "shoulder"}) == "unknown"


def test_compute_full_beep_time_offset_from_source_window() -> None:
    # Audit pinned the beep at 0.5 s into a clip that starts 22.4417 s into
    # the raw video. The full WAV starts 0.0 s into the raw video. Beep in
    # full-WAV coordinates is fws[0] - full[0] + clip_beep = 22.9417 s.
    full_beep = compute_full_beep_time(
        fixture_window_in_source=(22.4417, 67.2017),
        full_window_in_source=(0.0, 100.0),
        clip_beep_time=0.5,
    )
    assert full_beep == pytest.approx(22.9417)


def test_compute_full_beep_time_with_padded_full_window() -> None:
    # If the full WAV was extracted with --pre-pad 5 it starts 5 s before
    # the audited window; the beep should land 5 s deeper into the full WAV.
    full_beep = compute_full_beep_time(
        fixture_window_in_source=(22.0, 67.0),
        full_window_in_source=(17.0, 80.0),  # pre-pad 5 s
        clip_beep_time=0.5,
    )
    assert full_beep == pytest.approx(5.0 + 0.5)


def test_auto_tags_seed_from_camera_and_rounds() -> None:
    tags = auto_tags(
        camera_kind="head",
        ground_truth_in_full=12.0,
        stage_rounds={"plates": 3, "poppers": 0},
    )
    assert "headcam" in tags
    assert "late-beep" in tags
    assert "steel-prone" in tags
    assert "very-late-beep" not in tags


def test_auto_tags_marks_very_late_only_above_30s() -> None:
    tags = auto_tags(
        camera_kind="hand",
        ground_truth_in_full=45.0,
        stage_rounds={"plates": 0, "poppers": 0},
    )
    assert "handheld" in tags
    assert "very-late-beep" in tags
    assert "late-beep" not in tags  # mutually exclusive
    assert "steel-prone" not in tags


def test_auto_tags_no_full_track_skips_late_buckets() -> None:
    # Clip-only fixtures (iPhone with source_video=None) have no full beep
    # time. The late-beep tags require source-time info, so they shouldn't
    # appear -- the clip itself only spans 5 s of pre-beep padding.
    tags = auto_tags(camera_kind="hand", ground_truth_in_full=None, stage_rounds=None)
    assert tags == ["handheld"]


def test_evaluate_detection_marks_top1_within_tolerance() -> None:
    result = evaluate_detection(
        stem="x",
        track="clip",
        tags=["headcam"],
        ground_truth_s=1.0,
        tolerance_ms=100.0,
        detected_time_s=1.05,
        detected_score=42.0,
    )
    assert result.correct_top1 is True
    assert result.correct_in_topn is True
    assert result.error_s == pytest.approx(0.05)
    assert result.error_kind is None


def test_evaluate_detection_marks_topn_when_winner_wrong_but_runner_up_right() -> None:
    result = evaluate_detection(
        stem="x",
        track="clip",
        tags=[],
        ground_truth_s=1.0,
        tolerance_ms=50.0,
        detected_time_s=3.5,  # wrong
        detected_score=10.0,
        candidate_times_s=[3.5, 1.02, 8.0],  # second is right
    )
    assert result.correct_top1 is False
    assert result.correct_in_topn is True


def test_evaluate_detection_records_not_found() -> None:
    result = evaluate_detection(
        stem="x",
        track="clip",
        tags=[],
        ground_truth_s=1.0,
        tolerance_ms=50.0,
        detected_time_s=None,
        detected_score=None,
        error_kind="not_found",
    )
    assert result.correct_top1 is False
    assert result.correct_in_topn is False
    assert result.error_kind == "not_found"
    assert result.error_s is None


def test_summarize_aggregates_overall_and_per_tag() -> None:
    rows = [
        evaluate_detection(
            stem="a",
            track="clip",
            tags=["headcam"],
            ground_truth_s=1.0,
            tolerance_ms=50.0,
            detected_time_s=1.0,
            detected_score=10.0,
        ),
        evaluate_detection(
            stem="b",
            track="clip",
            tags=["handheld"],
            ground_truth_s=5.0,
            tolerance_ms=50.0,
            detected_time_s=None,
            detected_score=None,
            error_kind="not_found",
        ),
        evaluate_detection(
            stem="c",
            track="full",
            tags=["headcam", "late-beep"],
            ground_truth_s=20.0,
            tolerance_ms=100.0,
            detected_time_s=25.0,
            detected_score=5.0,
            candidate_times_s=[25.0, 20.05],
        ),
    ]
    summary = summarize(rows)
    assert summary.total == 3
    assert summary.top1_hits == 1
    assert summary.topn_hits == 2
    assert summary.not_found == 1
    assert summary.recall_top1 == pytest.approx(1 / 3)
    headcam = summary.by_tag["headcam"]
    assert headcam.total == 2
    assert headcam.top1_hits == 1
    assert headcam.topn_hits == 2
    handheld = summary.by_tag["handheld"]
    assert handheld.not_found == 1


def test_manifest_yaml_round_trip(tmp_path: Path) -> None:
    manifest = BeepCalibrationManifest(
        fixtures=[
            BeepFixtureEntry(
                stem="stage-a",
                camera_kind="head",
                clip_wav="stage-a.wav",
                ground_truth_in_clip=0.5,
                tags=["headcam"],
            ),
            BeepFixtureEntry(
                stem="stage-b",
                camera_kind="hand",
                clip_wav="stage-b.wav",
                ground_truth_in_clip=5.0,
                full_wav="full/stage-b_full.wav",
                ground_truth_in_full=27.5,
                full_duration_s=120.0,
                tags=["handheld", "late-beep"],
            ),
        ]
    )
    out = tmp_path / "manifest.yaml"
    save_manifest(manifest, out)
    loaded = load_manifest(out)
    assert loaded.model_dump() == manifest.model_dump()


def test_load_manifest_returns_empty_when_missing(tmp_path: Path) -> None:
    out = tmp_path / "missing.yaml"
    loaded = load_manifest(out)
    assert loaded.fixtures == []


def test_fixtures_with_full_audio_filters_to_existing_files(tmp_path: Path) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "stage-b_full.wav").write_bytes(b"")
    manifest = BeepCalibrationManifest(
        fixtures=[
            BeepFixtureEntry(
                stem="stage-a",
                camera_kind="head",
                clip_wav="stage-a.wav",
                ground_truth_in_clip=0.5,
                full_wav="full/stage-a_full.wav",  # missing on disk
                ground_truth_in_full=10.0,
            ),
            BeepFixtureEntry(
                stem="stage-b",
                camera_kind="hand",
                clip_wav="stage-b.wav",
                ground_truth_in_clip=5.0,
                full_wav="full/stage-b_full.wav",  # exists
                ground_truth_in_full=20.0,
            ),
            BeepFixtureEntry(
                stem="stage-c",
                camera_kind="head",
                clip_wav="stage-c.wav",
                ground_truth_in_clip=0.5,
                # no full_wav at all
            ),
        ]
    )
    rows = fixtures_with_full_audio(manifest, tmp_path)
    assert [r.stem for r in rows] == ["stage-b"]


def test_default_tolerance_constant_matches_audit_convention() -> None:
    # Most audit JSONs use tolerance_ms = 100 (~ 5 frames at 60 fps).
    # If this changes, callers need to know -- pin it.
    assert DEFAULT_TOLERANCE_MS == 100.0


def test_committed_manifest_references_existing_wavs(fixtures_dir: Path) -> None:
    """The committed manifest must stay in sync with the wavs on disk.

    Catches silent rot: a fixture rename or wav removal would otherwise
    only surface when ``eval_beep_detector.py`` runs. Full-track wavs
    are gitignored, so we only enforce existence for the clip wav and
    skip the full wav check when the developer hasn't extracted yet.
    """
    manifest_path = fixtures_dir / "beep_calibration" / "manifest.yaml"
    if not manifest_path.exists():
        pytest.skip("calibration manifest not built yet")
    manifest = load_manifest(manifest_path)
    assert manifest.fixtures, "manifest is empty -- run build_beep_calibration.py"
    for entry in manifest.fixtures:
        clip = fixtures_dir / entry.clip_wav
        assert clip.exists(), f"{entry.stem}: clip wav missing at {clip}"
        if entry.full_wav and entry.ground_truth_in_full is not None:
            assert (
                entry.ground_truth_in_full >= 0.0
            ), f"{entry.stem}: negative full beep time -- check fws/full window math"
