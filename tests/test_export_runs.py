"""Tests for the pure export-run record module (#629)."""

from __future__ import annotations

from datetime import UTC, datetime

from splitsmith import export_runs


def _run(run_id: str = "a" * 32, *, stage: int = 1) -> export_runs.ExportRun:
    return export_runs.ExportRun(
        run_id=run_id,
        kind="stage",
        finished_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        duration_seconds=12.5,
        stage_numbers=[stage],
        formats=["trim", "csv"],
        anomaly_count=0,
        artifacts=[export_runs.ExportArtifact(filename="stage1_x_trimmed.mp4", kind="trim")],
    )


def test_append_run_on_absent_doc_starts_a_log() -> None:
    doc = export_runs.append_run(None, _run())
    log = export_runs.load_log(doc)
    assert log.schema_version == export_runs.SCHEMA_VERSION
    assert [r.run_id for r in log.runs] == ["a" * 32]


def test_append_run_is_newest_first() -> None:
    doc = export_runs.append_run(None, _run("a" * 32, stage=1))
    doc = export_runs.append_run(doc, _run("b" * 32, stage=2))
    assert [r.run_id for r in export_runs.load_log(doc).runs] == ["b" * 32, "a" * 32]


def test_load_log_skips_an_unparseable_run_and_keeps_the_rest() -> None:
    """A malformed entry must not cost the whole history, and must not
    raise -- an export is never allowed to fail because bookkeeping is
    unreadable."""
    doc = export_runs.append_run(None, _run())
    doc["runs"].append({"run_id": "broken"})  # missing every required field
    log = export_runs.load_log(doc)
    assert [r.run_id for r in log.runs] == ["a" * 32]


def test_load_log_tolerates_a_doc_of_the_wrong_shape() -> None:
    assert export_runs.load_log(None).runs == []
    assert export_runs.load_log({}).runs == []
    assert export_runs.load_log({"runs": "not-a-list"}).runs == []


def test_new_run_id_is_unique() -> None:
    assert export_runs.new_run_id() != export_runs.new_run_id()


def test_stage_run_formats_lists_only_what_was_requested_in_pipeline_order() -> None:
    assert export_runs.stage_run_formats(trim=True, csv=False, fcpxml=True, report=True, overlay=False) == [
        "trim",
        "fcpxml",
        "report",
    ]
    assert (
        export_runs.stage_run_formats(trim=False, csv=False, fcpxml=False, report=False, overlay=False) == []
    )


def test_match_run_formats_carries_the_output_format_and_sidecar() -> None:
    assert export_runs.match_run_formats(output_format="mp4", youtube_sidecar=True) == [
        "mp4",
        "youtube-sidecar",
    ]
    assert export_runs.match_run_formats(output_format="fcpxml", youtube_sidecar=False) == ["fcpxml"]
