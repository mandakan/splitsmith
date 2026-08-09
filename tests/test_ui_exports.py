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
    ]
    assert rows[1][0] == "1"
    assert rows[2][0] == "2"


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


def test_slugify_matches_cli_format() -> None:
    """Filename slug parity: same shape as cli._slugify so exports
    produced via the SPA and via the CLI are byte-comparable."""
    assert exports_mod._slugify("Stage 1 -- H1") == "stage-1-h1"
    assert exports_mod._slugify("All Symbols!@#") == "all-symbols"
    assert exports_mod._slugify("") == "stage"


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
    from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

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
    from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

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
    from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

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
