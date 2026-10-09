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

from splitsmith import composition
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


def test_export_match_treats_a_missing_audit_as_zero_shots(tmp_path: Path) -> None:
    """One audit precondition across every surface (#619).

    ``exports.export_stage`` has treated an absent audit as zero shots since
    #612 -- a trim needs only a beep and a stage time. This composer still
    refused outright, so a match built from trim-only stages was impossible
    on the very stages the audit-free path exists to produce. Absent is fine
    and reported; the stage just loses its shot-dependent artefacts.
    """
    audit1 = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
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
                audit_path=tmp_path / "audit" / "nope.json",  # never written
                trimmed_path=trim2,
                beep_offset_seconds=5.0,
            ),
        ],
        request=_make_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    # Both stages ride the spine; the audit-less one is flagged, not dropped.
    assert result.stage_count == 2
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert [c.attrib["name"] for c in spine_clips] == ["Stage 1", "Stage 2"]
    assert any("stage 2" in a and "no shots audited" in a for a in result.anomalies)


def test_export_match_still_rejects_an_unparseable_audit(tmp_path: Path) -> None:
    """Absent means "never ran detection"; corrupt means something is wrong.
    Relaxing the first must not swallow the second."""
    bad = tmp_path / "audit" / "stage1.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    trim1 = _make_trim(tmp_path, "stage1_trimmed.mp4")

    with pytest.raises(match_exports_mod.MatchExportError, match="failed to read audit JSON"):
        match_exports_mod.export_match(
            stages=[
                match_exports_mod.MatchStageInput(
                    stage_number=1,
                    stage_name="Stage 1",
                    audit_path=bad,
                    trimmed_path=trim1,
                    beep_offset_seconds=5.0,
                )
            ],
            request=_make_request(),
            exports_dir=tmp_path / "exports",
            config=OutputConfig(),
            probe=_stub_probe,
        )


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


def test_export_match_overlay_requested_but_unrendered_emits_clear_anomaly(
    tmp_path: Path,
) -> None:
    """#217 -- when overlay is requested but the per-stage Generate
    didn't render one (overlay_path is None), the anomaly tells the
    user where to enable it rather than silently dropping the layer."""
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
                overlay_path=None,
            )
        ],
        request=_make_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("overlay not available" in a and "Overlay toggle" in a for a in result.anomalies)


def test_export_match_overlay_silent_for_shotless_stages(tmp_path: Path) -> None:
    """#217 -- shotless stages already surface a 'no shots audited'
    anomaly that mentions overlay; don't emit a second 'overlay not
    available' line for the same stage."""
    audit = _make_audit(tmp_path, "stage1.json", _audit_payload([]))
    trim = _make_trim(tmp_path, "stage1_trimmed.mp4")
    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit,
                trimmed_path=trim,
                beep_offset_seconds=5.0,
                overlay_path=None,
            )
        ],
        request=_make_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("no shots audited" in a for a in result.anomalies)
    assert not any("overlay not available" in a for a in result.anomalies)


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


def test_export_match_permissive_with_audit_with_no_shots(tmp_path: Path) -> None:
    """#214 -- a stage with empty ``shots[]`` no longer hard-fails. The
    spine still carries the stage as a trim-only segment; an anomaly
    flags the missing markers."""
    audit = _make_audit(tmp_path, "stage1.json", _audit_payload([]))
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
        request=_make_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert result.stage_count == 1
    assert result.fcpxml_path.exists()
    assert any("no shots audited" in a and "stage 1" in a for a in result.anomalies)
    # Spine still carries the stage; FCPXML just has no shot markers.
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 1
    markers = spine_clips[0].findall("marker")
    assert markers == []


def test_export_match_permissive_mixed_shotful_and_shotless_stages(tmp_path: Path) -> None:
    """When some stages have shots and others don't, the export proceeds
    end-to-end with markers only on the shotful ones."""
    audit_full = _make_audit(
        tmp_path,
        "stage1.json",
        _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]),
    )
    audit_empty = _make_audit(tmp_path, "stage2.json", _audit_payload([]))
    trim_a = _make_trim(tmp_path, "stage1_trimmed.mp4")
    trim_b = _make_trim(tmp_path, "stage2_trimmed.mp4")
    result = match_exports_mod.export_match(
        stages=[
            match_exports_mod.MatchStageInput(
                stage_number=1,
                stage_name="Stage 1",
                audit_path=audit_full,
                trimmed_path=trim_a,
                beep_offset_seconds=5.0,
            ),
            match_exports_mod.MatchStageInput(
                stage_number=2,
                stage_name="Stage 2",
                audit_path=audit_empty,
                trimmed_path=trim_b,
                beep_offset_seconds=5.0,
            ),
        ],
        request=_make_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert result.stage_count == 2
    # Anomaly only for the shotless stage.
    assert any("stage 2" in a and "no shots audited" in a for a in result.anomalies)
    assert not any("stage 1" in a and "no shots audited" in a for a in result.anomalies)
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 2
    # Stage 1 has a marker; stage 2 doesn't.
    assert clips[0].findall("marker")
    assert not clips[1].findall("marker")


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
        # #973: the renderer reports what it wrote.
        from splitsmith.mp4_render import Mp4RenderResult

        return Mp4RenderResult(output_path=output_path, duration_seconds=0.0)

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


# --- generated cards (issue #973) ------------------------------------------


def _one_stage_input(
    tmp_path: Path, *, expected_rounds: int | None = None
) -> match_exports_mod.MatchStageInput:
    audit = _make_audit(tmp_path, "stage1.json", _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]))
    return match_exports_mod.MatchStageInput(
        stage_number=1,
        stage_name="Stage 1",
        audit_path=audit,
        trimmed_path=_make_trim(tmp_path, "stage1_trimmed.mp4"),
        beep_offset_seconds=5.0,
        expected_rounds=expected_rounds,
    )


def _card_request(**overrides: object) -> match_exports_mod.MatchExportRequestData:
    fields: dict[str, object] = {
        "stage_numbers": (1,),
        "head_pad_seconds": 10.0,
        "tail_pad_seconds": 20.0,
        "include_secondaries": False,
        "include_overlay": False,
        "project_name": "Bromma Classifier",
        "output_format": "mp4",
    }
    fields.update(overrides)
    return match_exports_mod.MatchExportRequestData(**fields)  # type: ignore[arg-type]


def _capture_mp4(monkeypatch: pytest.MonkeyPatch, *, degradations: tuple[str, ...] = ()) -> dict[str, object]:
    from splitsmith import mp4_render

    captured: dict[str, object] = {}

    def fake_render_mp4(comp, *, output_path, **kwargs):  # type: ignore[no-untyped-def]
        captured["comp"] = comp
        captured["kwargs"] = kwargs
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return mp4_render.Mp4RenderResult(
            output_path=output_path, duration_seconds=123.5, degradations=degradations
        )

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)
    return captured


def test_title_page_reaches_the_mp4_composition_and_its_duration_is_the_renderers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture_mp4(monkeypatch)
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(
            title_page=True,
            title_page_info=("2026-05-01", "M. Axell"),
            title_page_duration_seconds=4.0,
            closing_card=True,
            overlay_theme="clean",
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.title_page.text == "Bromma Classifier"
    assert comp.title_page.info == ("2026-05-01", "M. Axell")
    assert comp.title_page.duration_seconds == 4.0
    assert comp.closing is not None and comp.closing.text == "Bromma Classifier"
    assert captured["kwargs"]["overlay_theme"] == "clean"
    # The timeline length is the renderer's figure, which counts the cards.
    assert result.duration_seconds == 123.5
    assert not any("ignored" in a for a in result.anomalies)


def test_mp4_renderer_degradations_become_anomalies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _capture_mp4(monkeypatch, degradations=("cards skipped: no browser",))
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_page=True),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert "cards skipped: no browser" in result.anomalies


def test_mp4_honours_slate_titles_with_the_round_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture_mp4(monkeypatch)
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path, expected_rounds=24)],
        request=_card_request(title_kind="slate", title_duration_seconds=2.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    title = captured["comp"].stages[0].title
    assert title is not None
    assert title.text == "Stage 1"
    assert title.style == "slate"
    assert title.info == ("24 rounds",)
    assert not any("titles ignored" in a for a in result.anomalies)


def test_no_round_count_means_no_info_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_kind="lower-third"),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].stages[0].title.info == ()


def test_mp4_honours_intro_and_outro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _capture_mp4(monkeypatch)
    intro = _make_trim(tmp_path, "intro.mp4")
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(intro_path=intro),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].intro is not None
    assert captured["comp"].intro.asset.path == intro
    assert not any("intro ignored" in a for a in result.anomalies)


def test_title_page_is_an_anomaly_on_fcpxml(tmp_path: Path) -> None:
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(output_format="fcpxml", title_page=True, closing_card=True),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("title page ignored" in a for a in result.anomalies)
    assert any("closing card ignored" in a for a in result.anomalies)
    assert result.fcpxml_path.exists()


def _two_stage_inputs(tmp_path: Path) -> list[match_exports_mod.MatchStageInput]:
    payload = _audit_payload([{"shot_number": 1, "ms_after_beep": 500}])
    return [
        match_exports_mod.MatchStageInput(
            stage_number=n,
            stage_name=f"Stage {n}",
            audit_path=_make_audit(tmp_path, f"stage{n}.json", payload),
            trimmed_path=_make_trim(tmp_path, f"stage{n}_trimmed.mp4"),
            beep_offset_seconds=5.0,
        )
        for n in (1, 2)
    ]


def test_an_xfade_kind_on_the_fcpxml_path_is_rendered_as_zoom_with_an_anomaly(tmp_path: Path) -> None:
    """Issue #1244, review focus 5: the frozen emitter knows zoom and
    static only; every other kind lowers to zoom and the response says so."""
    result = match_exports_mod.export_match(
        stages=_two_stage_inputs(tmp_path),
        request=_card_request(
            stage_numbers=(1, 2),
            output_format="fcpxml",
            transition_kind="dissolve",
            transition_duration_seconds=1.0,
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert "transition dissolve is not an FCP effect; the FCPXML uses zoom" in result.anomalies
    assert "Blurs" in result.fcpxml_path.read_text() or "Zoom" in result.fcpxml_path.read_text()


def test_a_sting_on_the_fcpxml_path_is_rendered_as_zoom_with_an_anomaly(tmp_path: Path) -> None:
    """Issue #1245: a sting is a Look template over the MP4 boundary; the
    FCPXML emitter has no such thing and substitutes zoom, saying so."""
    result = match_exports_mod.export_match(
        stages=_two_stage_inputs(tmp_path),
        request=_card_request(
            stage_numbers=(1, 2),
            output_format="fcpxml",
            transition_kind="sting:wipe",
            transition_duration_seconds=1.0,
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert "transition sting:wipe is not an FCP effect; the FCPXML uses zoom" in result.anomalies


def test_a_malformed_transition_kind_is_refused_by_the_request_model() -> None:
    """Issue #1245: ``TransitionKind`` is an open string now; the grammar
    check keeps the old ``Literal``'s refusals."""
    import pydantic

    from splitsmith.ui import exports_api

    for kind in ("sting:", "sting:Wipe", "wipe"):
        with pytest.raises(pydantic.ValidationError):
            exports_api.MatchExportRequest(stage_numbers=[1, 2], transition_kind=kind)
        with pytest.raises(pydantic.ValidationError):
            exports_api.CompareGridRequest(stage_numbers=[1, 2], audio_from="a", transition_kind=kind)
    accepted = exports_api.MatchExportRequest(stage_numbers=[1, 2], transition_kind="sting:wipe")
    assert accepted.transition_kind == "sting:wipe"


def test_mp4_export_passes_transitions_to_the_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #1244: the MP4 renderer draws transitions now, so the request
    layer hands them over instead of recording the old "ignored" anomaly."""
    captured = _capture_mp4(monkeypatch)
    result = match_exports_mod.export_match(
        stages=_two_stage_inputs(tmp_path),
        request=_card_request(
            stage_numbers=(1, 2), output_format="mp4", transition_kind="fade", transition_duration_seconds=1.0
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.transitions == (
        composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),
    )
    assert not any("transitions ignored" in a for a in result.anomalies)


def test_mp4_export_keeps_slates_with_transitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A boundary into a slate is the mechanism's point; only the FCPXML
    emitter refuses the pair."""
    captured = _capture_mp4(monkeypatch)
    result = match_exports_mod.export_match(
        stages=_two_stage_inputs(tmp_path),
        request=_card_request(
            stage_numbers=(1, 2), output_format="mp4", transition_kind="dissolve", title_kind="slate"
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.stages[0].title is not None and comp.stages[0].title.style == "slate"
    assert len(comp.transitions) == 1
    assert not any("slate titles dropped" in a for a in result.anomalies)


def test_fcp7xml_still_ignores_transitions(tmp_path: Path) -> None:
    result = match_exports_mod.export_match(
        stages=_two_stage_inputs(tmp_path),
        request=_card_request(stage_numbers=(1, 2), output_format="fcp7xml", transition_kind="zoom"),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("transitions ignored" in a for a in result.anomalies)


def test_fcp7xml_still_ignores_titles_and_intro(tmp_path: Path) -> None:
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(
            output_format="fcp7xml", title_kind="slate", intro_path=_make_trim(tmp_path, "intro.mp4")
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("titles ignored" in a for a in result.anomalies)
    assert any("intro ignored" in a for a in result.anomalies)


def test_title_info_lines_come_from_the_project(tmp_path: Path) -> None:
    from datetime import date

    from splitsmith.match_project import MatchProject

    project = MatchProject(name="Bromma", competitor_name="M. Axell", match_date=date(2026, 5, 1))
    assert match_exports_mod.title_info_lines(project, extra="Production Optics") == (
        "2026-05-01",
        "M. Axell",
        "Production Optics",
    )
    bare = MatchProject(name="Bromma")
    assert match_exports_mod.title_info_lines(bare, extra="  ") == ()


def test_title_info_lines_print_the_shooters_club_after_their_name(tmp_path: Path) -> None:
    """The one place the club line (#1243) is drawn today: under the
    shooter's name on the title page, before the division."""
    from datetime import date

    from splitsmith.identity import ShooterIdentity
    from splitsmith.match_project import MatchProject

    project = MatchProject(
        name="Bromma",
        competitor_name="M. Axell",
        match_date=date(2026, 5, 1),
        identity=ShooterIdentity(club="Bromma PK"),
    )
    assert match_exports_mod.title_info_lines(project, division="Production Optics") == (
        "2026-05-01",
        "M. Axell",
        "Bromma PK",
        "Production Optics",
    )
    assert match_exports_mod.title_info_lines(
        MatchProject(name="B", identity=ShooterIdentity(club="PK"))
    ) == ("PK",)


def test_stage_inputs_for_project_reads_existing_artefacts(tmp_path: Path) -> None:
    """The one assembler the server job and the CLI verb share: per-stage
    paths under ``exports/`` and ``audit/``, clip-local beep, secondaries
    with a beep, the overlay path, and the round count for the slate."""
    from splitsmith.config import StageRounds
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    project = MatchProject(
        name="m",
        trim_pre_buffer_seconds=5.0,
        stages=[
            StageEntry(
                stage_number=3,
                stage_name="Speed",
                time_seconds=8.0,
                stage_rounds=StageRounds(expected=24),
                videos=[
                    StageVideo(path=Path("p.mp4"), role="primary", beep_time=12.0),
                    StageVideo(path=Path("c.mp4"), role="secondary", beep_time=2.0),
                    StageVideo(path=Path("n.mp4"), role="secondary", beep_time=None),
                ],
            )
        ],
    )
    inputs = match_exports_mod.stage_inputs_for_project(project, tmp_path, [3])
    assert len(inputs) == 1
    stage = inputs[0]
    assert stage.stage_number == 3
    assert stage.stage_name == "Speed"
    assert stage.trimmed_path == tmp_path / "exports" / "stage3_speed_trimmed.mp4"
    assert stage.overlay_path == tmp_path / "exports" / "stage3_speed_overlay.mov"
    assert stage.audit_path == tmp_path / "audit" / "stage3.json"
    # Clip-local beep: the pre-buffer, unless the beep sat inside it.
    assert stage.beep_offset_seconds == 5.0
    cam_id = project.stages[0].videos[1].video_id
    assert [s.video_id for s in stage.secondaries] == [cam_id]
    assert stage.secondaries[0].beep_offset_seconds == 2.0
    assert (
        stage.secondaries[0].trimmed_path == tmp_path / "exports" / f"stage3_speed_cam_{cam_id}_trimmed.mp4"
    )
    assert stage.expected_rounds == 24
    # The summary's inputs (#972) ride along from the same StageEntry.
    assert stage.stage_time_seconds == 8.0
    assert stage.stage_time_is_manual is False
    assert stage.scorecard is None
    assert stage.stage_rounds is not None and stage.stage_rounds.expected == 24


# --- summary hold (issue #972) ---------------------------------------------


def test_summary_hold_reaches_the_mp4_composition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from splitsmith.match_project import StageScorecard

    captured = _capture_mp4(monkeypatch)
    stage = _one_stage_input(tmp_path)
    stage = match_exports_mod.MatchStageInput(
        **{
            **stage.__dict__,
            "scorecard": StageScorecard(hit_factor=12.0, alphas=10),
            "stage_time_seconds": 4.5,
        }
    )
    result = match_exports_mod.export_match(
        stages=[stage],
        request=_card_request(summary_hold_seconds=3.0, shooter_label="M. Axell"),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    hold = captured["comp"].stages[0].summary
    assert hold is not None
    assert hold.duration_seconds == 3.0
    assert hold.label == "M. Axell"
    assert hold.data.stage_time_seconds == 4.5
    assert hold.data.scorecard is not None and hold.data.scorecard.hit_factor == 12.0
    # Shots come from the audit, measured from the beep, splits re-derived.
    assert [s.time_from_beep for s in hold.data.shots] == [0.5]
    assert not any("summary" in a for a in result.anomalies)


def test_summary_hold_label_falls_back_to_the_project_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(summary_hold_seconds=2.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].stages[0].summary.label == "Bromma Classifier"


def test_summary_hold_carries_the_stages_confirmed_reloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hold's tile carries the confirmed reloads the summary's reload
    row draws; an auto proposal never reaches it (spec 2026-10-08, part 2)."""
    captured = _capture_mp4(monkeypatch)
    stage = _one_stage_input(tmp_path)
    doc = json.loads(stage.audit_path.read_text())
    doc["events"] = [
        {"id": "evt-1", "kind": "reload", "start": 1.0, "end": 2.25, "source": "manual"},
        {"id": "evt-2", "kind": "reload", "start": 3.0, "end": 4.0, "source": "auto"},
    ]
    stage.audit_path.write_text(json.dumps(doc))
    match_exports_mod.export_match(
        stages=[stage],
        request=_card_request(summary_hold_seconds=3.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    hold = captured["comp"].stages[0].summary
    assert [(r.event_id, r.duration) for r in hold.data.reloads] == [("evt-1", 1.25)]


def test_summary_hold_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].stages[0].summary is None


def test_summary_hold_is_an_anomaly_on_the_xml_renderers(tmp_path: Path) -> None:
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(output_format="fcpxml", summary_hold_seconds=3.0),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("summary hold ignored" in a for a in result.anomalies)


def test_shotless_stage_keeps_its_chapter_and_the_sidecar_says_what_is_missing(tmp_path: Path) -> None:
    """A stage with a reviewed beep and a time but no audited shots still gets
    its YouTube chapter (chapters are per stage, not per shot) and the
    sidecar's caption file simply has no cues for it. The anomaly names
    exactly what the stage lost: shot markers and the overlay, not chapters.
    """
    from splitsmith import youtube_sidecar

    audit1 = _make_audit(tmp_path, "stage1.json", _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]))
    trim1 = _make_trim(tmp_path, "stage1_trimmed.mp4")
    trim2 = _make_trim(tmp_path, "stage2_trimmed.mp4")
    request = match_exports_mod.MatchExportRequestData(
        stage_numbers=(1, 2),
        head_pad_seconds=1.0,
        tail_pad_seconds=1.0,
        include_secondaries=False,
        include_overlay=False,
        project_name="Bromma",
        youtube_sidecar=True,
    )
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
                audit_path=tmp_path / "audit" / "nope.json",
                trimmed_path=trim2,
                beep_offset_seconds=5.0,
            ),
        ],
        request=request,
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    sidecar = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(result.fcpxml_path))
    assert [c["title"] for c in sidecar.chapters] == ["Stage 1", "Stage 2"]
    anomaly = next(a for a in result.anomalies if "stage 2" in a)
    assert "no shots audited" in anomaly
    assert "chapter" not in anomaly  # it kept its chapter
    assert "shot markers" in anomaly and "overlay" in anomaly


def test_stage_inputs_name_an_unnamed_stage_by_its_number(tmp_path: Path) -> None:
    """SSI often has no stage names (Hostfinalen XI had eight blanks); the
    slate then showed only the round count. The inputs carry "Stage N",
    with N the stage's own number, so the slate, the chapters and the
    FCPXML clip names all say it, also for a non-contiguous selection."""
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    project = MatchProject(name="Hostfinalen XI")
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=name,
            time_seconds=10.0,
            videos=[StageVideo(path=Path(f"raw/v{n}.mp4"), role="primary", beep_time=5.0)],
        )
        for n, name in ((2, ""), (5, "   "), (7, "Standards"))
    ]
    inputs = match_exports_mod.stage_inputs_for_project(project, tmp_path, [2, 5, 7])
    assert [i.stage_name for i in inputs] == ["Stage 2", "Stage 5", "Standards"]
    # The file names keep the stored name: a re-export must find the trims.
    assert inputs[0].trimmed_path.name == "stage2_stage_trimmed.mp4"
    titles = match_exports_mod._build_uniform_titles(kind="slate", duration=1.5, stage_inputs=inputs)
    assert [t.text for t in titles.values()] == ["Stage 2", "Stage 5", "Standards"]


def test_card_variant_reaches_every_generated_card(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One knob (#1242): the title page, the closing card and every stage
    title carry the requested Look variant; a request without it draws
    the default."""
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_page=True, closing_card=True, title_kind="slate", card_variant="rise"),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.title_page.variant == "rise" and comp.closing.variant == "rise"
    assert all(stage.title is not None and stage.title.variant == "rise" for stage in comp.stages)

    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_page=True, title_kind="lower-third"),
        exports_dir=tmp_path / "exports2",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.title_page.variant == "default"
    assert all(stage.title is not None and stage.title.variant == "default" for stage in comp.stages)


def test_the_shooters_identity_reaches_the_composition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from splitsmith.composition import CompositionShooter
    from splitsmith.identity import ResolvedIdentity

    captured = _capture_mp4(monkeypatch)
    resolved = ResolvedIdentity(label="Mathias", accent="#123456", logo_path=tmp_path / "l.png", club="PK")
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_page=True, shooter_identity=resolved),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.shooters == (
        CompositionShooter(label="Mathias", accent="#123456", logo_path=tmp_path / "l.png", club="PK"),
    )
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_page=True),
        exports_dir=tmp_path / "exports2",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].shooters == ()


def test_per_slot_variants_reach_their_cards_and_fall_back_to_the_knob(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Slice 6 (#1246): each card slot has its own variant field; ``None``
    means the ``card_variant`` knob the CLI sets."""
    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(
            title_page=True,
            closing_card=True,
            title_kind="slate",
            card_variant="rise",
            stage_card_variant="default",
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.title_page.variant == "rise" and comp.closing.variant == "rise"
    assert all(stage.title is not None and stage.title.variant == "default" for stage in comp.stages)

    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(
            title_page=True, closing_card=True, title_kind="slate", title_page_variant="rise"
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert comp.title_page.variant == "rise" and comp.closing.variant == "default"
    assert all(stage.title is not None and stage.title.variant == "default" for stage in comp.stages)

    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(title_kind="lower-third", stage_card_variant="rise"),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    assert all(stage.title is not None and stage.title.variant == "rise" for stage in comp.stages)


def test_the_export_request_accepts_any_installed_look_and_names_them_on_a_miss() -> None:
    import pydantic

    from splitsmith.ui import exports_api

    assert exports_api.MatchExportRequest(stage_numbers=[1], overlay_theme="clean").overlay_theme == "clean"
    with pytest.raises(pydantic.ValidationError, match="splitsmith, clean"):
        exports_api.MatchExportRequest(stage_numbers=[1], overlay_theme="nope")
    with pytest.raises(pydantic.ValidationError, match="splitsmith, clean"):
        exports_api.CompareGridRequest(stage_numbers=[1], audio_from="a", overlay_theme="nope")
    with pytest.raises(pydantic.ValidationError, match="splitsmith, clean"):
        exports_api.ExportStageRequest(overlay_theme="nope")
    request = exports_api.MatchExportRequest(
        stage_numbers=[1], title_page_variant="rise", stage_card_variant="default"
    )
    assert (request.title_page_variant, request.stage_card_variant, request.closing_card_variant) == (
        "rise",
        "default",
        None,
    )
