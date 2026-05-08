"""Tests for the UI match-export orchestrator (issue #171).

Pure orchestration over already-trimmed per-stage artefacts; the FCPXML
composer itself is exercised in ``test_fcpxml_gen.py``. ffprobe is stubbed
so the suite doesn't shell out.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from splitsmith.config import OutputConfig, VideoMetadata
from splitsmith.fcpxml_gen import FFprobeError
from splitsmith.ui import match_exports as match_exports_mod


def _meta_30fps(duration: float = 20.0) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=duration,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _audit_payload(shots: list[dict]) -> dict:
    return {
        "stage_number": 1,
        "stage_name": "Stage 1",
        "stage_time_seconds": 8.0,
        "beep_time": 5.0,
        "shots": shots,
        "_candidates_pending_audit": {"candidates": []},
    }


def _make_trim(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def _make_audit(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _stub_probe(_path: Path) -> VideoMetadata:
    return _meta_30fps()


def _make_request(
    *,
    head_pad: float = 5.0,
    tail_pad: float = 5.0,
    include_secondaries: bool = True,
    include_overlay: bool = True,
    project_name: str = "match",
) -> match_exports_mod.MatchExportRequestData:
    return match_exports_mod.MatchExportRequestData(
        stage_numbers=(1, 2),
        head_pad_seconds=head_pad,
        tail_pad_seconds=tail_pad,
        include_secondaries=include_secondaries,
        include_overlay=include_overlay,
        project_name=project_name,
    )


def test_export_match_produces_stitched_fcpxml(tmp_path: Path) -> None:
    audit1 = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    audit2 = _make_audit(
        tmp_path,
        "stage2.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 1000}]),
    )
    trim1 = _make_trim(tmp_path, "stage1_trimmed.mp4")
    trim2 = _make_trim(tmp_path, "stage2_trimmed.mp4")

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit1,
                trimmed_path=trim1,
                beep_offset_seconds=5.0,
            ),
            match_exports_mod.MatchStageInput(
                stage_number=2,
                stage_name="Stage 2",
                audit_path=audit2,
                trimmed_path=trim2,
                beep_offset_seconds=5.0,
            ),
        ],
        request=_make_request(head_pad=10.0, tail_pad=20.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert result.stage_count == 2
    assert result.fcpxml_path.exists()
    assert result.fcpxml_path.name == "match-match.fcpxml"
    # Two 20s clips with no shrink -> 40s total.
    assert result.duration_seconds == pytest.approx(40.0)
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 2
    assert spine_clips[0].attrib["name"] == "Stage 1"
    assert spine_clips[1].attrib["name"] == "Stage 2"


def test_export_match_action_cut_padding_shrinks_total_duration(tmp_path: Path) -> None:
    """head=0.5, tail=1.0 against a 20s clip with beep at 5s and last shot
    at 0.5s past beep collapses each stage to ~2.0s on the timeline."""
    payload = _audit_payload([{"shot_number": 1, "ms_after_beep": 500}])
    audit = _make_audit(tmp_path, "stage1.json", payload)
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
            )
        ],
        request=match_exports_mod.MatchExportRequestData(
            stage_numbers=(1,),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            include_secondaries=True,
            include_overlay=True,
            project_name="match",
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    # head_trim = 5.0 - 0.5 = 4.5s; tail_avail = 20 - 5.5 = 14.5; tail_trim
    # = 14.5 - 1.0 = 13.5s; eff = 20 - 4.5 - 13.5 = 2.0s.
    assert result.duration_seconds == pytest.approx(2.0)


def test_export_match_includes_secondaries_when_flag_set(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    cam_trim = _make_trim(tmp_path, "stage1_cam_abc_trimmed.mp4")

    stage = match_exports_mod.MatchStageInput(
        stage_number=1,
        stage_name="Stage 1",
        audit_path=audit,
        trimmed_path=trim,
        beep_offset_seconds=5.0,
        secondaries=(
            match_exports_mod.MatchSecondaryInput(
                video_id="abc",
                trimmed_path=cam_trim,
                beep_offset_seconds=5.0,
                label="Cam abc",
            ),
        ),
    )
    result = match_exports_mod.export_match(
        stages=[stage],
        request=_make_request(head_pad=10.0, tail_pad=20.0, include_secondaries=True),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert len(nested) == 1
    assert nested[0].attrib["name"] == "Cam abc"


def test_export_match_drops_secondaries_when_flag_off(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    cam_trim = _make_trim(tmp_path, "stage1_cam_abc_trimmed.mp4")

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
                secondaries=(
                    match_exports_mod.MatchSecondaryInput(
                        video_id="abc",
                        trimmed_path=cam_trim,
                        beep_offset_seconds=5.0,
                    ),
                ),
            )
        ],
        request=_make_request(head_pad=10.0, tail_pad=20.0, include_secondaries=False),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert nested == []


def test_export_match_records_anomaly_for_missing_secondary(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    # No file at this path -- the orchestrator should warn but not raise.
    cam_trim = tmp_path / "missing_cam.mp4"

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
                secondaries=(
                    match_exports_mod.MatchSecondaryInput(
                        video_id="abc",
                        trimmed_path=cam_trim,
                        beep_offset_seconds=5.0,
                    ),
                ),
            )
        ],
        request=_make_request(head_pad=10.0, tail_pad=20.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("cam abc" in a for a in result.anomalies)
    assert result.fcpxml_path.exists()


def test_export_match_raises_on_missing_trim(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    with pytest.raises(match_exports_mod.MatchExportError, match="lossless trim missing"):
        match_exports_mod.export_match(
            stages=[
                match_exports_mod.MatchStageInput(
                    stage_number=1,
                    stage_name="Stage 1",
                    audit_path=audit,
                    trimmed_path=tmp_path / "missing_trim.mp4",
                    beep_offset_seconds=5.0,
                )
            ],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=_stub_probe,
        )


def test_export_match_raises_on_missing_audit(tmp_path: Path) -> None:
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    with pytest.raises(match_exports_mod.MatchExportError, match="audit JSON missing"):
        match_exports_mod.export_match(
            stages=[
                match_exports_mod.MatchStageInput(
                    stage_number=1,
                    stage_name="Stage 1",
                    audit_path=tmp_path / "missing_audit.json",
                    trimmed_path=trim,
                    beep_offset_seconds=5.0,
                )
            ],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=_stub_probe,
        )


def test_export_match_raises_on_audit_with_no_shots(tmp_path: Path) -> None:
    audit = _make_audit(tmp_path, "stage1.json", _audit_payload([]))
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    with pytest.raises(match_exports_mod.MatchExportError, match="no shots"):
        match_exports_mod.export_match(
            stages=[
                match_exports_mod.MatchStageInput(
                    stage_number=1,
                    stage_name="Stage 1",
                    audit_path=audit,
                    trimmed_path=trim,
                    beep_offset_seconds=5.0,
                )
            ],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=_stub_probe,
        )


def test_export_match_raises_on_empty_stages(tmp_path: Path) -> None:
    with pytest.raises(match_exports_mod.MatchExportError, match="at least one stage"):
        match_exports_mod.export_match(
            stages=[],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=_stub_probe,
        )


def test_export_match_wraps_ffprobe_failure(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")

    def boom(_path: Path) -> VideoMetadata:
        raise FFprobeError("simulated probe failure")

    with pytest.raises(match_exports_mod.MatchExportError, match="ffprobe failed"):
        match_exports_mod.export_match(
            stages=[
                match_exports_mod.MatchStageInput(
                    stage_number=1,
                    stage_name="Stage 1",
                    audit_path=audit,
                    trimmed_path=trim,
                    beep_offset_seconds=5.0,
                )
            ],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=boom,
        )


def test_export_match_slugifies_project_name_for_filename(tmp_path: Path) -> None:
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
            )
        ],
        request=match_exports_mod.MatchExportRequestData(
            stage_numbers=(1,),
            head_pad_seconds=10.0,
            tail_pad_seconds=20.0,
            include_secondaries=True,
            include_overlay=True,
            project_name="Region Cup -- 2026 (May)",
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert result.fcpxml_path.name == "region-cup-2026-may-match.fcpxml"


# --- youtube preset (#204 layer 2) ---------------------------------------


def test_youtube_preset_anomaly_when_renderer_is_not_mp4(tmp_path: Path) -> None:
    """The preset only applies to the MP4 renderer; setting it together
    with an FCPXML / FCP7 export must not silently mis-encode anything
    -- it surfaces as an anomaly so the user sees the mismatch."""
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
            )
        ],
        request=match_exports_mod.MatchExportRequestData(
            stage_numbers=(1,),
            head_pad_seconds=10.0,
            tail_pad_seconds=20.0,
            include_secondaries=True,
            include_overlay=True,
            project_name="match",
            output_format="fcpxml",
            youtube_preset=True,
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("youtube encode preset ignored" in a for a in result.anomalies)


def test_youtube_preset_threads_through_to_mp4_renderer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user picks output_format=mp4 with youtube_preset=True
    the orchestrator forwards the flag to ``mp4_render.render_mp4``."""
    audit = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")

    captured: dict[str, object] = {}

    def fake_render_mp4(comp, *, output_path, **kwargs):  # type: ignore[no-untyped-def]
        captured["youtube_preset"] = kwargs.get("youtube_preset")
        # Touch the output so downstream code that checks existence works.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)

    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
            )
        ],
        request=match_exports_mod.MatchExportRequestData(
            stage_numbers=(1,),
            head_pad_seconds=10.0,
            tail_pad_seconds=20.0,
            include_secondaries=True,
            include_overlay=True,
            project_name="match",
            output_format="mp4",
            youtube_preset=True,
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["youtube_preset"] is True
    # No anomaly when the preset matches the renderer.
    assert not any("youtube encode preset" in a for a in result.anomalies)
    assert result.fcpxml_path.suffix == ".mp4"
