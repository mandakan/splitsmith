"""Tests for the UI export pipeline (issue #17).

Covers the audit-JSON -> engine-Shot conversion, slug parity with the CLI,
and the orchestrator's failure modes (missing audit, no shots).
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from splitsmith.audit_data import StageExportError
from splitsmith.config import Config, StageData
from splitsmith.ui import exports as exports_mod


def _audit_payload(shots: list[dict] | None = None, beep_in_clip: float = 5.0) -> dict:
    return {
        "stage_number": 1,
        "stage_name": "Stage 1 -- H1",
        "stage_time_seconds": 8.0,
        "beep_time": beep_in_clip,
        "shots": shots if shots is not None else [],
        "_candidates_pending_audit": {
            "candidates": [
                {
                    "candidate_number": 1,
                    "time": 5.5,
                    "ms_after_beep": 500,
                    "peak_amplitude": 0.7,
                    "confidence": 0.9,
                },
                {
                    "candidate_number": 2,
                    "time": 5.9,
                    "ms_after_beep": 900,
                    "peak_amplitude": 0.6,
                    "confidence": 0.85,
                },
            ]
        },
    }


def test_export_stage_writes_csv_and_report(tmp_path: Path) -> None:
    """End-to-end: drop a real audit JSON, get a CSV byte-for-byte
    consistent with the CLI's output for the same shots."""
    audit_path = tmp_path / "audit" / "stage1.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                    {"shot_number": 2, "candidate_number": 2, "time": 5.9, "ms_after_beep": 900},
                ]
            )
        ),
        encoding="utf-8",
    )

    exports_dir = tmp_path / "exports"

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=True,
            write_fcpxml=False,
            write_report=True,
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.shots_written == 2
    assert result.csv_path is not None
    assert result.csv_path.exists()
    assert result.report_path is not None
    assert result.report_path.exists()
    # CSV name must match the CLI slug.
    assert result.csv_path.name == "stage1_stage-1-h1_splits.csv"
    # CSV content sanity.
    rows = list(csv.reader(result.csv_path.open()))
    assert rows[0] == [
        "shot_number",
        "time_from_start",
        "split",
        "peak_amplitude",
        "confidence",
        "notes",
        "moving",
    ]
    assert rows[1][0] == "1"
    assert rows[2][0] == "2"
    # No confirmed regions on this stage -- moving is false throughout.
    assert rows[1][-1] == "false"
    assert rows[2][-1] == "false"


def test_export_stage_missing_audit_no_longer_refuses(tmp_path: Path) -> None:
    """Superseded by the missing-audit tests below: a stage that never ran
    shot detection is a legitimate state, not a fault. This regression
    guard replaces the old hard-refusal assertion (this task's change) --
    it still exercises the no-source, all-artefacts-requested path, just
    without expecting an exception."""
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(stage_number=1),
        audit_path=tmp_path / "missing.json",
        exports_dir=tmp_path / "exports",
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="S",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.shots_written == 0
    assert result.csv_path is None
    assert result.trimmed_video_path is None


def test_export_stage_permissive_with_empty_shots(tmp_path: Path) -> None:
    """#214 -- empty ``shots[]`` no longer hard-fails. The export
    proceeds, skipping CSV / overlay (those require shots), but the
    report still ships and surfaces "No shots detected" via the
    standard anomaly pipeline. CSV / overlay skips also land as
    anomalies so the user sees what was suppressed."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text(json.dumps(_audit_payload(shots=[])), encoding="utf-8")
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=True,
            write_overlay=True,
            write_fcpxml=False,
            write_report=True,
        ),
        audit_path=audit_path,
        exports_dir=tmp_path / "exports",
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="S",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.shots_written == 0
    assert result.csv_path is None
    assert result.overlay_path is None
    assert result.report_path is not None
    assert result.report_path.exists()
    # CSV / overlay skips are surfaced; the standard "no shots"
    # anomaly piggybacks via report.detect_anomalies.
    assert any("csv not written: no shots audited" in a for a in result.anomalies)
    assert any("overlay not written: no shots audited" in a for a in result.anomalies)
    assert any("No shots detected" in a for a in result.anomalies)


def test_export_stage_missing_audit_writes_trim(tmp_path: Path, monkeypatch) -> None:
    """A stage that never ran shot detection still exports its lossless
    trim: beep + stage time are the only real prerequisites (#214 made
    empty shots[] permissive, but the gate above it was unreachable)."""
    source = tmp_path / "GX010042.MP4"
    source.write_bytes(b"not really video")
    calls: list[dict] = []

    def fake_trim_video(src, dst, **kwargs):
        calls.append({"src": src, "dst": dst, **kwargs})
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")

    monkeypatch.setattr(exports_mod.trim, "trim_video", fake_trim_video)

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=tmp_path / "audit" / "stage1.json",  # deliberately absent
        exports_dir=tmp_path / "exports",
        source_video_path=source,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="El Prez",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.trimmed_video_path is not None
    assert result.trimmed_video_path.exists()
    assert result.shots_written == 0
    assert calls[0]["beep_time"] == 10.0
    assert calls[0]["stage_time"] == 8.0
    assert calls[0]["mode"] == "lossless"


def test_export_stage_missing_audit_skips_csv_with_reason(tmp_path: Path, monkeypatch) -> None:
    """Asking for CSV without shot data is not an error -- the trim ships
    and the CSV skip is surfaced as an anomaly, same as an empty shots[]."""
    source = tmp_path / "GX010042.MP4"
    source.write_bytes(b"not really video")
    monkeypatch.setattr(
        exports_mod.trim,
        "trim_video",
        lambda src, dst, **kw: dst.write_bytes(b"trimmed"),
    )

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=True,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=tmp_path / "audit" / "stage1.json",
        exports_dir=tmp_path / "exports",
        source_video_path=source,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="El Prez",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.csv_path is None
    assert result.trimmed_video_path is not None
    assert any("csv not written: no shots audited" in a for a in result.anomalies)


def test_export_stage_corrupt_audit_still_raises(tmp_path: Path) -> None:
    """A malformed audit file is a real fault -- distinct from 'detection
    never ran' -- and must not be silently treated as zero shots."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StageExportError, match="failed to read audit JSON"):
        exports_mod.export_stage(
            request=exports_mod.StageExportRequest(stage_number=1, write_trim=False),
            audit_path=audit_path,
            exports_dir=tmp_path / "exports",
            source_video_path=None,
            pre_buffer_seconds=5.0,
            post_buffer_seconds=5.0,
            stage_data=StageData(
                stage_number=1,
                stage_name="El Prez",
                time_seconds=8.0,
                scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
            ),
            beep_time_in_source=10.0,
            config=Config(),
        )


def test_export_stage_skips_trim_and_fcpxml_when_source_unreachable(tmp_path: Path) -> None:
    """Source video missing (USB unplugged) -> trim and FCPXML skip with a
    helpful anomaly, but CSV / report still write so the user gets the
    audit data even when external storage is offline."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                ]
            )
        ),
        encoding="utf-8",
    )

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=True,
            write_fcpxml=True,
            write_report=True,
        ),
        audit_path=audit_path,
        exports_dir=tmp_path / "exports",
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="S",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.csv_path and result.csv_path.exists()
    assert result.report_path and result.report_path.exists()
    assert result.trimmed_video_path is None
    assert result.fcpxml_path is None
    # Both the trim-skip and fcpxml-skip messages should reference the
    # source-unreachable cause, not raw ffmpeg errors.
    assert any("trim not written" in a for a in result.anomalies)
    assert any("fcpxml not written" in a for a in result.anomalies)


def test_export_stage_trims_secondaries_and_records_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each secondary cam gets its own ``stage<N>_<slug>_cam_<id>_trimmed.mp4``
    and the result records the per-cam paths so the SPA / FCPXML can wire
    them up. The ffmpeg call is stubbed to avoid shelling out (#54)."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                ]
            )
        ),
        encoding="utf-8",
    )

    primary_src = tmp_path / "primary.mp4"
    primary_src.write_bytes(b"")
    cam_a_src = tmp_path / "cam_a.mp4"
    cam_a_src.write_bytes(b"")
    cam_b_src = tmp_path / "cam_b.mp4"
    cam_b_src.write_bytes(b"")

    from splitsmith import trim as trim_module
    from splitsmith.config import TrimResult

    captured: list[tuple[Path, Path]] = []

    def fake_trim_video(input_path: Path, output_path: Path, **kwargs: Any) -> TrimResult:
        captured.append((input_path, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return TrimResult(output_path=output_path, start_time=0.0, end_time=20.0)

    monkeypatch.setattr(trim_module, "trim_video", fake_trim_video)
    monkeypatch.setattr(exports_mod.trim, "trim_video", fake_trim_video)

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=audit_path,
        exports_dir=tmp_path / "exports",
        source_video_path=primary_src,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
        secondaries=[
            exports_mod.SecondaryExport(video_id="aaaaaa", source_path=cam_a_src, beep_time_in_source=11.0),
            exports_mod.SecondaryExport(video_id="bbbbbb", source_path=cam_b_src, beep_time_in_source=9.5),
        ],
    )

    # 1 primary + 2 secondaries = 3 ffmpeg calls.
    assert len(captured) == 3
    sec_outputs = {p.name for _, p in captured}
    assert "stage1_stage-1-h1_trimmed.mp4" in sec_outputs
    assert "stage1_stage-1-h1_cam_aaaaaa_trimmed.mp4" in sec_outputs
    assert "stage1_stage-1-h1_cam_bbbbbb_trimmed.mp4" in sec_outputs

    assert set(result.secondary_trimmed_paths) == {"aaaaaa", "bbbbbb"}
    for vid, p in result.secondary_trimmed_paths.items():
        assert p.exists()
        assert p.name == f"stage1_stage-1-h1_cam_{vid}_trimmed.mp4"


def test_export_stage_skips_secondary_when_source_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Secondary source missing (USB unplugged, file deleted between
    Generate clicks) -> the cam is dropped with an anomaly explaining what
    happened. The primary's export is unaffected."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                ]
            )
        ),
        encoding="utf-8",
    )

    primary_src = tmp_path / "primary.mp4"
    primary_src.write_bytes(b"")

    from splitsmith import trim as trim_module
    from splitsmith.config import TrimResult

    def fake_trim_video(input_path: Path, output_path: Path, **kwargs: Any) -> TrimResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return TrimResult(output_path=output_path, start_time=0.0, end_time=20.0)

    monkeypatch.setattr(trim_module, "trim_video", fake_trim_video)
    monkeypatch.setattr(exports_mod.trim, "trim_video", fake_trim_video)

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=True,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=audit_path,
        exports_dir=tmp_path / "exports",
        source_video_path=primary_src,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="S",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
        secondaries=[
            exports_mod.SecondaryExport(
                video_id="ghost",
                source_path=tmp_path / "ghost.mp4",  # never created
                beep_time_in_source=11.0,
            ),
        ],
    )

    assert result.trimmed_video_path is not None and result.trimmed_video_path.exists()
    assert result.secondary_trimmed_paths == {}
    assert any("secondary cam ghost" in a for a in result.anomalies)


def test_export_overview_status(tmp_path: Path) -> None:
    """The MatchProject.export_overview reports per-stage status correctly."""
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="Stage 1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        )
    )
    project.stages[0].videos.append(
        StageVideo(
            path=Path("raw/a.mp4"),
            role="primary",
            beep_time=1.0,
            processed={"beep": True, "shot_detect": True, "trim": True},
        )
    )
    audit = root / "audit" / "stage1.json"
    audit.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                ]
            )
        ),
        encoding="utf-8",
    )
    overview = project.export_overview(root)
    assert len(overview) == 1
    row = overview[0]
    assert row.has_primary
    assert row.audit_shot_count == 1
    # Total candidate pool from the detector. NOT "pending" -- once shot
    # detection has run, every candidate is kept (in shots[]) or rejected.
    # The fixture ships 2 candidates; only 1 was promoted to a shot, so
    # 1 was implicitly rejected.
    assert row.total_candidate_count == 2
    assert row.ready_to_export is True
    # A trim needs the beep + the stage time only -- no audit, no shots (#613).
    assert row.ready_to_trim is True
    assert row.has_exports is False
    # source_reachable is False -- the test fixture's primary path
    # ``raw/a.mp4`` doesn't exist on disk, mirroring the "USB unplugged"
    # case the SPA badges with "Source missing".
    assert row.source_reachable is False
    # Single-cam stage -- secondaries roster is empty.
    assert row.secondaries == []


def test_export_overview_surfaces_secondaries(tmp_path: Path) -> None:
    """Every secondary on the stage shows up in ``StageExportStatus.secondaries``,
    flagged with beep / source / trim state so the SPA can render the multi-cam
    panel without having to cross-reference the project + filesystem itself."""
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        )
    )
    # Primary present + processed so we land in the multi-cam-ready state.
    primary_src = root / "raw" / "a.mp4"
    primary_src.parent.mkdir(parents=True, exist_ok=True)
    primary_src.write_bytes(b"")
    project.stages[0].videos.extend(
        [
            StageVideo(
                path=Path("raw/a.mp4"),
                role="primary",
                beep_time=1.0,
                beep_reviewed=True,
                processed={"beep": True, "shot_detect": True, "trim": True},
            ),
            # Eligible: beep + reachable source + a stale trim from a prior run.
            StageVideo(
                path=Path("raw/cam_ready.mp4"),
                role="secondary",
                beep_time=2.0,
                beep_reviewed=True,
            ),
            # Beep set but unreviewed -- still eligible to ship; SPA flags it.
            StageVideo(
                path=Path("raw/cam_unreviewed.mp4"),
                role="secondary",
                beep_time=3.0,
                beep_reviewed=False,
            ),
            # Source missing (no file on disk) -- ineligible.
            StageVideo(
                path=Path("raw/cam_missing.mp4"),
                role="secondary",
                beep_time=4.0,
            ),
            # No beep yet -- ineligible until the user runs detect / sets one.
            StageVideo(
                path=Path("raw/cam_no_beep.mp4"),
                role="secondary",
            ),
            # Ignored videos must not leak into the secondaries roster.
            StageVideo(path=Path("raw/cam_ignored.mp4"), role="ignored"),
        ]
    )
    # Materialise the two cams whose sources should resolve, plus a stale
    # per-cam trim for the "ready" one so we can prove ``trim_present`` /
    # ``trim_path`` flow through.
    (root / "raw" / "cam_ready.mp4").write_bytes(b"")
    (root / "raw" / "cam_unreviewed.mp4").write_bytes(b"")
    cam_ready_id = project.stages[0].videos[1].video_id
    base = "stage1_stage-1-h1"
    stale_trim = root / "exports" / f"{base}_cam_{cam_ready_id}_trimmed.mp4"
    stale_trim.parent.mkdir(parents=True, exist_ok=True)
    stale_trim.write_bytes(b"stale")

    audit = root / "audit" / "stage1.json"
    audit.write_text(
        json.dumps(
            _audit_payload(
                shots=[
                    {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
                ]
            )
        ),
        encoding="utf-8",
    )

    overview = project.export_overview(root)
    row = overview[0]
    by_path = {s.path.name: s for s in row.secondaries}
    # Ignored videos are filtered; the four secondaries each get an entry.
    assert set(by_path) == {
        "cam_ready.mp4",
        "cam_unreviewed.mp4",
        "cam_missing.mp4",
        "cam_no_beep.mp4",
    }

    ready = by_path["cam_ready.mp4"]
    assert ready.has_beep and ready.source_reachable
    assert ready.beep_reviewed is True
    assert ready.trim_present and ready.trim_path == stale_trim

    unreviewed = by_path["cam_unreviewed.mp4"]
    assert unreviewed.has_beep and unreviewed.source_reachable
    assert unreviewed.beep_reviewed is False
    assert unreviewed.trim_present is False and unreviewed.trim_path is None

    missing = by_path["cam_missing.mp4"]
    assert missing.has_beep and missing.source_reachable is False

    nobeep = by_path["cam_no_beep.mp4"]
    assert nobeep.has_beep is False

    # The stale per-cam trim alone is enough to flip ``has_exports`` true,
    # since the SPA's "Exported" badge should reflect any export artefact
    # on disk -- not just primary outputs.
    assert row.has_exports is True
    assert row.last_export_at is not None


def test_export_overview_ready_to_trim_branches(tmp_path: Path) -> None:
    """``ready_to_trim`` is the one rule the CLI, the server and the SPA all
    read (#613): not skipped, a primary with a beep, a positive stage time.

    Deliberately *not* part of it: an audit, shots, or a reachable source.
    A bare trim needs none of the first two, and reachability is reported
    separately so the SPA can badge "source missing" on an otherwise
    exportable row rather than hiding it.
    """
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    def _stage(n: int, **kw: object) -> StageEntry:
        stage = StageEntry(
            stage_number=n,
            stage_name=f"Stage {n}",
            time_seconds=float(kw.pop("time_seconds", 8.0)),  # type: ignore[arg-type]
            skipped=bool(kw.pop("skipped", False)),
        )
        if kw.pop("primary", True):
            stage.videos.append(
                StageVideo(
                    path=Path(f"raw/s{n}.mp4"),
                    role="primary",
                    beep_time=kw.pop("beep_time", 1.0),  # type: ignore[arg-type]
                )
            )
        assert not kw, f"unused kwargs: {kw}"
        return stage

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages = [
        _stage(1),  # ready: beep + time, no audit, no scorecard timestamp
        _stage(2, beep_time=None),  # no beep
        _stage(3, time_seconds=0.0),  # untouched placeholder
        _stage(4, skipped=True),  # explicitly skipped
        _stage(5, primary=False),  # no primary at all
    ]
    ready = {r.stage_number: r.ready_to_trim for r in project.export_overview(root)}
    assert ready == {1: True, 2: False, 3: False, 4: False, 5: False}
    # Stage 1 is trim-ready without any of the shot-detection prerequisites
    # ``ready_to_export`` insists on -- the two flags are not the same gate.
    row1 = next(r for r in project.export_overview(root) if r.stage_number == 1)
    assert row1.ready_to_export is False
    assert row1.source_reachable is False


def test_export_stage_request_accepts_secondary_video_ids() -> None:
    """``ExportStageRequest`` round-trips the new allowlist field. ``None``
    keeps the legacy "include every cam with a beep" default; an empty list
    forces zero secondaries; a populated list narrows to the named cams."""
    from splitsmith.ui.server import ExportStageRequest

    default = ExportStageRequest()
    assert default.secondary_video_ids is None

    explicit_none = ExportStageRequest.model_validate({"secondary_video_ids": None})
    assert explicit_none.secondary_video_ids is None

    empty = ExportStageRequest.model_validate({"secondary_video_ids": []})
    assert empty.secondary_video_ids == []

    subset = ExportStageRequest.model_validate({"secondary_video_ids": ["aaa", "bbb"]})
    assert subset.secondary_video_ids == ["aaa", "bbb"]


# --- summary card (issue #972, option 1) -----------------------------------


def _seed_stage_with_trim(tmp_path: Path, *, shots: bool = True) -> tuple[Path, Path]:
    audit_path = tmp_path / "audit" / "stage1.json"
    audit_path.parent.mkdir(parents=True)
    payload = _audit_payload(
        shots=[{"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500}] if shots else []
    )
    audit_path.write_text(json.dumps(payload), encoding="utf-8")
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    (exports_dir / "stage1_stage-1-h1_trimmed.mp4").write_bytes(b"")
    return audit_path, exports_dir


def _export(audit_path: Path, exports_dir: Path, **kwargs: Any) -> exports_mod.StageExportResult:
    from splitsmith.match_project import StageScorecard

    return exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
            write_summary_card=True,
            summary_hold_seconds=2.5,
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
        scorecard=StageScorecard(hit_factor=12.0, alphas=10),
        shooter_label="M. Axell",
        **kwargs,
    )


def test_summary_card_is_written_beside_the_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import summary_card

    captured: dict[str, Any] = {}

    def fake_render(**kwargs: Any) -> summary_card.SummaryCardResult:
        captured.update(kwargs)
        kwargs["png_path"].write_bytes(b"")
        kwargs["mov_path"].write_bytes(b"")
        return summary_card.SummaryCardResult(
            png_path=kwargs["png_path"], mov_path=kwargs["mov_path"], degradations=("no browser: x",)
        )

    monkeypatch.setattr(exports_mod.summary_card, "render_summary_card", fake_render)
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    result = _export(audit_path, exports_dir)
    assert result.summary_card_path == exports_dir / "stage1_stage-1-h1_summary.mov"
    assert captured["png_path"] == exports_dir / "stage1_stage-1-h1_summary.png"
    assert captured["seconds"] == 2.5
    assert captured["label"] == "M. Axell"
    data = captured["data"]
    assert data.stage_time_seconds == 8.0
    assert data.scorecard is not None and data.scorecard.hit_factor == 12.0
    assert [s.time_from_beep for s in data.shots] == [0.5]
    assert captured["trimmed_video_path"] == exports_dir / "stage1_stage-1-h1_trimmed.mp4"
    # A degradation is worth telling the user about, not a failure.
    assert any("no browser" in a for a in result.anomalies)
    assert not result.export_failures


def test_summary_card_carries_the_stages_confirmed_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import summary_card

    captured: dict[str, Any] = {}

    def fake_render(**kwargs: Any) -> summary_card.SummaryCardResult:
        captured.update(kwargs)
        return summary_card.SummaryCardResult(png_path=kwargs["png_path"], mov_path=kwargs["mov_path"])

    monkeypatch.setattr(exports_mod.summary_card, "render_summary_card", fake_render)
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    doc = json.loads(audit_path.read_text())
    doc["events"] = [
        {"id": "evt-1", "kind": "reload", "start": 1.0, "end": 2.25, "source": "manual"},
        {"id": "evt-2", "kind": "reload", "start": 3.0, "end": 4.0, "source": "auto"},
    ]
    audit_path.write_text(json.dumps(doc))
    _export(audit_path, exports_dir)
    assert [(r.event_id, r.duration) for r in captured["data"].reloads] == [("evt-1", 1.25)]


def test_summary_card_skips_without_shots(tmp_path: Path) -> None:
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path, shots=False)
    result = _export(audit_path, exports_dir)
    assert result.summary_card_path is None
    assert any("summary card not written: no shots audited" in a for a in result.export_failures)


def test_summary_card_skips_without_a_trim(tmp_path: Path) -> None:
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    (exports_dir / "stage1_stage-1-h1_trimmed.mp4").unlink()
    result = _export(audit_path, exports_dir)
    assert result.summary_card_path is None
    assert any("summary card not written" in a and "trim" in a for a in result.export_failures)


def test_summary_card_failure_is_recorded_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import summary_card

    def boom(**kwargs: Any) -> summary_card.SummaryCardResult:
        raise summary_card.SummaryCardError("prores exploded")

    monkeypatch.setattr(exports_mod.summary_card, "render_summary_card", boom)
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    result = _export(audit_path, exports_dir)
    assert result.summary_card_path is None
    assert any("summary card not written: prores exploded" in a for a in result.export_failures)


def test_summary_card_off_by_default_touches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import summary_card

    def boom(**kwargs: Any) -> summary_card.SummaryCardResult:
        raise AssertionError("must not be called")

    monkeypatch.setattr(exports_mod.summary_card, "render_summary_card", boom)
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=False, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.summary_card_path is None


def test_export_overview_ready_to_export_bare_needs_a_reviewed_beep_and_a_time(tmp_path: Path) -> None:
    """``ready_to_export_bare`` is the bundle-export gate for a stage that has
    not been audited: the trim rule plus a *reviewed* beep. Every trim
    boundary and chapter time hangs off the beep, so an unreviewed auto
    beep is not enough, while shots are not required at all. Strictly
    between ``ready_to_trim`` and ``ready_to_export``.
    """
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    def _stage(n: int, *, reviewed: bool, beep: float | None = 1.0, time: float = 8.0) -> StageEntry:
        stage = StageEntry(stage_number=n, stage_name=f"Stage {n}", time_seconds=time)
        stage.videos.append(
            StageVideo(path=Path(f"raw/s{n}.mp4"), role="primary", beep_time=beep, beep_reviewed=reviewed)
        )
        return stage

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages = [
        _stage(1, reviewed=True),
        _stage(2, reviewed=False),
        _stage(3, reviewed=True, beep=None),
        _stage(4, reviewed=True, time=0.0),
    ]
    rows = {r.stage_number: r for r in project.export_overview(root)}
    assert {n: r.ready_to_export_bare for n, r in rows.items()} == {1: True, 2: False, 3: False, 4: False}
    assert rows[1].ready_to_trim is True and rows[1].ready_to_export is False
    assert rows[2].ready_to_trim is True  # the trim rule does not care about review


# --- the overlay style and its record (template HUD, slice 3) ------------------


def _overlay_export(
    audit_path: Path,
    exports_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fall_back: bool = False,
    **request: Any,
) -> tuple[exports_mod.StageExportResult, dict[str, Any]]:
    from splitsmith import overlay_render

    captured: dict[str, Any] = {}

    def fake_render(**kwargs: Any) -> Path:
        captured.update(kwargs)
        if fall_back:
            kwargs["degraded"].append(f"overlay style {kwargs['variant']!r} fell back to Classic: boom")
        kwargs["output_path"].write_bytes(b"mov")
        return kwargs["output_path"]

    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
            write_overlay=True,
            **request,
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    return result, captured


def test_the_overlay_style_reaches_the_renderer_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.overlay_hud import HudOptions

    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    options = HudOptions(landing=False, position="top-right")
    result, captured = _overlay_export(
        audit_path, exports_dir, monkeypatch, overlay_variant="plate", overlay_options=options
    )
    assert captured["variant"] == "plate" and captured["hud_options"] == options
    record = exports_dir / "stage1_stage-1-h1_overlay.json"
    assert result.overlay_settings_path == record
    assert json.loads(record.read_text())["variant"] == "plate"
    assert json.loads(record.read_text())["options"]["position"] == "top-right"


def test_a_fallback_is_recorded_as_classic_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MOV on disk is Classic, so the record says so: the next match
    export asking for the style re-renders rather than reusing it."""
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    result, _ = _overlay_export(audit_path, exports_dir, monkeypatch, fall_back=True, overlay_variant="plate")
    record = json.loads((exports_dir / "stage1_stage-1-h1_overlay.json").read_text())
    assert record["variant"] == "default" and record["options"] == {}
    assert any("fell back to Classic" in a for a in result.anomalies)
    assert not result.export_failures


def test_read_overlay_settings_takes_a_missing_record_as_the_defaults(tmp_path: Path) -> None:
    from splitsmith.overlay_hud import LEGACY_OVERLAY_SETTINGS

    assert exports_mod.read_overlay_settings(tmp_path / "nope.json") == LEGACY_OVERLAY_SETTINGS
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert exports_mod.read_overlay_settings(broken) is None


def test_a_template_record_without_the_template_identity_matches_nothing() -> None:
    """A HUD style's record written before it carried the template identity
    cannot say which template drew it: the reuse check misses, whether the
    request names the style or (the MCP tool) wants whatever was drawn.
    A Classic record carries no identity and still matches."""
    from splitsmith.overlay_hud import HudOptions, overlay_settings

    def settings(variant: str) -> dict:
        return overlay_settings(
            look="splitsmith",
            variant=variant,
            options=HudOptions(),
            codec="auto",
            max_height=None,
            max_fps=None,
            audit_revision="rev",
        )

    current = settings("plate")
    assert current["template"]
    legacy = {k: v for k, v in current.items() if k != "template"}
    assert exports_mod.overlay_record_matches(current, audit_revision="rev", wanted=current)
    assert exports_mod.overlay_record_matches(current, audit_revision="rev")
    assert not exports_mod.overlay_record_matches(legacy, audit_revision="rev", wanted=current)
    assert not exports_mod.overlay_record_matches(legacy, audit_revision="rev")
    assert not exports_mod.overlay_record_matches(current, audit_revision="other")
    assert not exports_mod.overlay_record_matches(current, audit_revision=None)
    classic = settings("default")
    assert "template" not in classic
    assert exports_mod.overlay_record_matches(classic, audit_revision="rev")


def test_a_template_record_with_no_template_identity_never_matches() -> None:
    """A template style whose template could not be found records
    ``"template": null``; re-deriving that record (the MCP tool's
    ``wanted=None``) finds no template either, and null == null must not
    read as a match: nothing vouches for what that MOV shows."""
    from splitsmith.overlay_hud import HudOptions, overlay_settings

    record = overlay_settings(
        look="nosuch",
        variant="plate",
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision="rev",
    )
    assert "template" in record and record["template"] is None
    assert not exports_mod.overlay_record_matches(record, audit_revision="rev")
    assert not exports_mod.overlay_record_matches(record, audit_revision="rev", wanted=dict(record))


@pytest.mark.parametrize("variant", ["default", "plate"])
def test_the_record_holds_the_audit_revision_read_before_the_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """An edit that lands while the overlay renders was never drawn, so the
    record names the audit as it stood when the render began: the next
    match export sees the edit as a change and draws again."""
    from splitsmith import overlay_render

    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    before = exports_mod.overlay_audit_revision(audit_path)
    assert before is not None

    def render_while_editing(**kwargs: Any) -> Path:
        doc = json.loads(audit_path.read_text(encoding="utf-8"))
        doc["shots"][0]["ms_after_beep"] = 640
        audit_path.write_text(json.dumps(doc), encoding="utf-8")
        kwargs["output_path"].write_bytes(b"mov")
        return kwargs["output_path"]

    monkeypatch.setattr(overlay_render, "render_overlay", render_while_editing)
    exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
            write_overlay=True,
            overlay_variant=variant,
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    record = json.loads((exports_dir / "stage1_stage-1-h1_overlay.json").read_text())
    assert record["audit_revision"] == before
    assert exports_mod.overlay_audit_revision(audit_path) != before


def test_the_record_holds_the_template_read_before_the_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template saved while the overlay renders (the desktop template
    editor can) was never drawn: the record names the template as it stood
    when the render began, so the next export sees the edit and draws again.
    Edits a copy of the shipped Looks, never the shipped files."""
    import shutil

    from splitsmith import looks, overlay_render
    from splitsmith.overlay_hud import HudOptions, overlay_settings

    copy = tmp_path / "shipped-looks"
    shutil.copytree(looks.shipped_looks_dir(), copy)
    monkeypatch.setattr(looks, "shipped_looks_dir", lambda: copy)
    audit_path, exports_dir = _seed_stage_with_trim(tmp_path)
    template = copy / "splitsmith" / "hud-plate.html"

    def render_while_editing(**kwargs: Any) -> Path:
        template.write_bytes(template.read_bytes() + b"\n<!-- saved mid-render -->\n")
        kwargs["output_path"].write_bytes(b"mov")
        return kwargs["output_path"]

    monkeypatch.setattr(overlay_render, "render_overlay", render_while_editing)
    exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=False,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
            write_overlay=True,
            overlay_variant="plate",
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    record = exports_mod.read_overlay_settings(exports_dir / "stage1_stage-1-h1_overlay.json")
    revision = exports_mod.overlay_audit_revision(audit_path)
    wanted = overlay_settings(
        look="splitsmith",
        variant="plate",
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision=revision,
    )
    assert not exports_mod.overlay_record_matches(record, audit_revision=revision, wanted=wanted)
    assert not exports_mod.overlay_record_matches(record, audit_revision=revision)


def test_the_audit_revision_is_none_for_an_unreadable_audit(tmp_path: Path) -> None:
    broken = tmp_path / "stage1.json"
    broken.write_text("{not json", encoding="utf-8")
    assert exports_mod.overlay_audit_revision(broken) is None
    # A missing audit is zero shots, a real state with a real revision.
    assert exports_mod.overlay_audit_revision(tmp_path / "absent.json") is not None


# --- stage events: moving column, events.csv, FCPXML region markers -------
# (spec 2026-10-08, part 2)


def _event(event_id: str, kind: str, start: float, end: float, source: str = "manual") -> dict:
    return {"id": event_id, "kind": kind, "start": start, "end": end, "source": source}


def test_export_stage_moving_column_true_inside_a_confirmed_movement_region(tmp_path: Path) -> None:
    audit_path = tmp_path / "stage1.json"
    doc = _audit_payload(
        shots=[
            {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
            {"shot_number": 2, "candidate_number": 2, "time": 9.0, "ms_after_beep": 4000},
        ]
    )
    doc["events"] = [_event("evt-1", "movement", 3.0, 6.0, source="manual")]
    audit_path.write_text(json.dumps(doc), encoding="utf-8")

    exports_dir = tmp_path / "exports"
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.csv_path is not None
    rows = list(csv.reader(result.csv_path.open()))
    assert rows[0][-1] == "moving"
    # Shot 1 at 0.5s is outside [3.0, 6.0]; shot 2 at 4.0s is inside.
    assert rows[1][-1] == "false"
    assert rows[2][-1] == "true"

    events_csv = exports_dir / "stage1_stage-1-h1_events.csv"
    assert events_csv.exists()
    events_rows = list(csv.reader(events_csv.open()))
    assert events_rows[0] == ["id", "kind", "start", "end", "duration", "source", "note"]
    assert events_rows[1] == ["evt-1", "movement", "3.000", "6.000", "3.000", "manual", ""]


def test_export_stage_with_no_confirmed_regions_writes_no_events_csv(tmp_path: Path) -> None:
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                shots=[{"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500}]
            )
        ),
        encoding="utf-8",
    )
    exports_dir = tmp_path / "exports"
    exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert not (exports_dir / "stage1_stage-1-h1_events.csv").exists()


def test_export_stage_an_auto_only_proposal_writes_nothing_new(tmp_path: Path) -> None:
    """An unconfirmed auto proposal must not surface anywhere: moving stays
    false and no events.csv is written, the same as no events at all."""
    audit_path = tmp_path / "stage1.json"
    doc = _audit_payload(
        shots=[{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    )
    doc["events"] = [_event("evt-1", "movement", 3.0, 6.0, source="auto")]
    audit_path.write_text(json.dumps(doc), encoding="utf-8")

    exports_dir = tmp_path / "exports"
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.csv_path is not None
    rows = list(csv.reader(result.csv_path.open()))
    assert rows[1][-1] == "false"  # shot at 4.0s is inside the region, but it's only auto
    assert not (exports_dir / "stage1_stage-1-h1_events.csv").exists()


def test_export_stage_fcpxml_carries_a_confirmed_region_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import fcpxml_gen as fcpxml_mod
    from splitsmith.config import VideoMetadata

    def fake_probe(_path: Path) -> VideoMetadata:
        return VideoMetadata(
            width=1920, height=1080, duration_seconds=20.0, frame_rate_num=30, frame_rate_den=1
        )

    monkeypatch.setattr(fcpxml_mod, "probe_video", fake_probe)

    audit_path = tmp_path / "stage1.json"
    doc = _audit_payload(
        shots=[{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    )
    doc["events"] = [_event("evt-1", "reload", 8.05, 9.47, source="manual")]
    audit_path.write_text(json.dumps(doc), encoding="utf-8")

    exports_dir = tmp_path / "exports"
    exports_dir.mkdir(parents=True)
    # A stale lossless trim stands in for a real one (write_trim=False
    # takes the "candidate" branch in export_stage).
    (exports_dir / "stage1_stage-1-h1_trimmed.mp4").write_bytes(b"")

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=False, write_fcpxml=True, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.fcpxml_path is not None
    text = result.fcpxml_path.read_text(encoding="utf-8")
    assert "Reload 1.42" in text


def test_export_stage_fcpxml_with_no_confirmed_regions_matches_output_without_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identity pin: a stage with an unconfirmed (auto) region writes
    an FCPXML identical to a stage with no events at all."""
    from splitsmith import fcpxml_gen as fcpxml_mod
    from splitsmith.config import VideoMetadata

    def fake_probe(_path: Path) -> VideoMetadata:
        return VideoMetadata(
            width=1920, height=1080, duration_seconds=20.0, frame_rate_num=30, frame_rate_den=1
        )

    monkeypatch.setattr(fcpxml_mod, "probe_video", fake_probe)

    exports_dir = tmp_path / "exports"
    exports_dir.mkdir(parents=True)
    audit_path = exports_dir / "audit.json"
    (exports_dir / "stage1_stage-1-h1_trimmed.mp4").write_bytes(b"")

    def _run(doc: dict) -> bytes:
        audit_path.write_text(json.dumps(doc), encoding="utf-8")
        result = exports_mod.export_stage(
            request=exports_mod.StageExportRequest(
                stage_number=1, write_trim=False, write_csv=False, write_fcpxml=True, write_report=False
            ),
            audit_path=audit_path,
            exports_dir=exports_dir,
            source_video_path=None,
            pre_buffer_seconds=5.0,
            post_buffer_seconds=5.0,
            stage_data=StageData(
                stage_number=1,
                stage_name="Stage 1 -- H1",
                time_seconds=8.0,
                scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
            ),
            beep_time_in_source=10.0,
            config=Config(),
        )
        assert result.fcpxml_path is not None
        return result.fcpxml_path.read_bytes()

    shots = [{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    doc_no_events = _audit_payload(shots=shots)
    doc_auto_only = _audit_payload(shots=shots)
    doc_auto_only["events"] = [_event("evt-1", "reload", 8.05, 9.47, source="auto")]

    bytes_no_events = _run(doc_no_events)
    bytes_auto_only = _run(doc_auto_only)
    assert bytes_no_events == bytes_auto_only


@pytest.mark.parametrize(
    "corrupt_events",
    [
        pytest.param(
            [{"id": "evt-1", "kind": "movement", "start": 6.0, "end": 3.0, "source": "manual"}],
            id="end_before_start",
        ),
        pytest.param({"not": "a list"}, id="a_dict_instead_of_a_list"),
        pytest.param(42, id="an_int_instead_of_a_list"),
    ],
)
def test_export_stage_corrupt_events_field_exports_moving_false_and_no_events_csv(
    tmp_path: Path, corrupt_events: object
) -> None:
    """A malformed ``events`` field -- an event with ``end < start``, the
    field holding a dict instead of a list, or an int -- must not fail
    the export. It degrades to no confirmed regions: ``moving`` reads
    false for every shot (including one that would have fallen inside the
    bad region had it been valid) and no ``events.csv`` is written."""
    audit_path = tmp_path / "stage1.json"
    doc = _audit_payload(
        shots=[{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    )
    doc["events"] = corrupt_events
    audit_path.write_text(json.dumps(doc), encoding="utf-8")

    exports_dir = tmp_path / "exports"
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.csv_path is not None
    rows = list(csv.reader(result.csv_path.open()))
    assert rows[1][-1] == "false"
    assert result.events_csv_path is None
    assert not (exports_dir / "stage1_stage-1-h1_events.csv").exists()


def test_export_stage_records_events_csv_path_on_the_result(tmp_path: Path) -> None:
    """``events_csv_path`` on the result surfaces the file the same way
    ``csv_path`` / ``fcpxml_path`` do, so a caller can offer it as a
    download without re-deriving the filename."""
    audit_path = tmp_path / "stage1.json"
    doc = _audit_payload(
        shots=[{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    )
    doc["events"] = [_event("evt-1", "movement", 3.0, 6.0, source="manual")]
    audit_path.write_text(json.dumps(doc), encoding="utf-8")

    exports_dir = tmp_path / "exports"
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )
    assert result.events_csv_path == exports_dir / "stage1_stage-1-h1_events.csv"
    assert result.events_csv_path.exists()


def test_export_stage_removes_a_stale_events_csv_when_regions_are_dropped(tmp_path: Path) -> None:
    """A re-export after the user deletes the stage's last confirmed
    region must not leave the previous run's events.csv behind -- a
    download route serving it would hand out a region that no longer
    exists (#review: important finding 1)."""
    audit_path = tmp_path / "stage1.json"
    exports_dir = tmp_path / "exports"

    def _export() -> exports_mod.StageExportResult:
        return exports_mod.export_stage(
            request=exports_mod.StageExportRequest(
                stage_number=1, write_trim=False, write_csv=True, write_fcpxml=False, write_report=False
            ),
            audit_path=audit_path,
            exports_dir=exports_dir,
            source_video_path=None,
            pre_buffer_seconds=5.0,
            post_buffer_seconds=5.0,
            stage_data=StageData(
                stage_number=1,
                stage_name="Stage 1 -- H1",
                time_seconds=8.0,
                scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
            ),
            beep_time_in_source=10.0,
            config=Config(),
        )

    shots = [{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    doc_with_region = _audit_payload(shots=shots)
    doc_with_region["events"] = [_event("evt-1", "movement", 3.0, 6.0, source="manual")]
    audit_path.write_text(json.dumps(doc_with_region), encoding="utf-8")
    first = _export()
    events_csv = exports_dir / "stage1_stage-1-h1_events.csv"
    assert first.events_csv_path == events_csv
    assert events_csv.exists()

    # The user deleted the region; re-export with none left.
    doc_without_region = _audit_payload(shots=shots)
    doc_without_region["events"] = []
    audit_path.write_text(json.dumps(doc_without_region), encoding="utf-8")
    second = _export()
    assert second.events_csv_path is None
    assert not events_csv.exists()
    assert second.removed_paths == [events_csv]


def _csv_export(tmp_path: Path, *, write_csv: bool) -> exports_mod.StageExportResult:
    return exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1, write_trim=False, write_csv=write_csv, write_fcpxml=False, write_report=False
        ),
        audit_path=tmp_path / "stage1.json",
        exports_dir=tmp_path / "exports",
        source_video_path=None,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="Stage 1 -- H1",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )


def _write_audit_with_region(audit_path: Path, *, shots: list[dict]) -> None:
    doc = _audit_payload(shots=shots)
    doc["events"] = [_event("evt-1", "movement", 3.0, 6.0, source="manual")]
    audit_path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_csv_export_with_no_shots_removes_both_earlier_csvs(tmp_path: Path) -> None:
    """#1331: a ``write_csv`` re-export after every shot was removed left the
    previous run's splits and events CSVs on disk, so a download served
    figures for shots that no longer exist."""
    shots = [{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    _write_audit_with_region(tmp_path / "stage1.json", shots=shots)
    first = _csv_export(tmp_path, write_csv=True)
    assert first.csv_path is not None and first.csv_path.exists()
    assert first.events_csv_path is not None and first.events_csv_path.exists()

    _write_audit_with_region(tmp_path / "stage1.json", shots=[])
    second = _csv_export(tmp_path, write_csv=True)

    assert second.csv_path is None
    assert second.events_csv_path is None
    assert not first.csv_path.exists()
    assert not first.events_csv_path.exists()
    assert second.removed_paths == [first.csv_path, first.events_csv_path]


def test_an_export_without_csv_leaves_earlier_csvs_alone(tmp_path: Path) -> None:
    """``write_csv`` off is not a statement about the CSVs: like every other
    output a run was not asked for, the earlier files stay."""
    shots = [{"shot_number": 1, "candidate_number": 1, "time": 9.0, "ms_after_beep": 4000}]
    _write_audit_with_region(tmp_path / "stage1.json", shots=shots)
    first = _csv_export(tmp_path, write_csv=True)

    _write_audit_with_region(tmp_path / "stage1.json", shots=[])
    second = _csv_export(tmp_path, write_csv=False)

    assert first.csv_path is not None and first.csv_path.exists()
    assert first.events_csv_path is not None and first.events_csv_path.exists()
    assert second.removed_paths == []
