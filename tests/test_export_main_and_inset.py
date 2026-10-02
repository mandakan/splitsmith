"""The picture and its inset in the single-shooter match export
(2026-10-02): a main camera chosen per stage by mount or role, one inset in
a corner, every other angle carried switched off for editing, and the
overlay dropped (said so) when the main camera's trim starts off the
primary's."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from splitsmith.config import OutputConfig
from splitsmith.ui import match_exports as m
from tests.test_ui_match_exports import _audit_payload, _make_audit, _make_trim, _stub_probe


def _stage(tmp_path: Path, *, phone_offset: float = 5.0, overlay: bool = False) -> m.MatchStageInput:
    audit = _make_audit(tmp_path, "stage1.json", _audit_payload([{"shot_number": 1, "ms_after_beep": 500}]))
    overlay_path = None
    if overlay:
        overlay_path = tmp_path / "stage1_overlay.mov"
        overlay_path.write_bytes(b"ov")
    return m.MatchStageInput(
        stage_number=1,
        stage_name="Stage 1",
        audit_path=audit,
        trimmed_path=_make_trim(tmp_path, "stage1_trimmed.mp4"),
        beep_offset_seconds=5.0,
        primary_mount="head",
        overlay_path=overlay_path,
        secondaries=(
            m.MatchSecondaryInput(
                video_id="phone",
                trimmed_path=_make_trim(tmp_path, "stage1_cam_phone_trimmed.mp4"),
                beep_offset_seconds=phone_offset,
                label="Cam phone",
                mount="hand",
            ),
            m.MatchSecondaryInput(
                video_id="glasses",
                trimmed_path=_make_trim(tmp_path, "stage1_cam_glasses_trimmed.mp4"),
                beep_offset_seconds=5.0,
                label="Cam glasses",
                mount=None,
            ),
        ),
    )


def _request(**over) -> m.MatchExportRequestData:
    base = {
        "stage_numbers": (1,),
        "head_pad_seconds": 1.0,
        "tail_pad_seconds": 1.0,
        "include_secondaries": True,
        "include_overlay": True,
        "project_name": "match",
    }
    base.update(over)
    return m.MatchExportRequestData(**base)


def _export(tmp_path: Path, stage: m.MatchStageInput, request: m.MatchExportRequestData):
    return m.export_match(
        stages=[stage],
        request=request,
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )


def test_choose_stage_cameras_by_mount_then_role_never_the_picture_twice(tmp_path: Path) -> None:
    cams = m.stage_cameras(_stage(tmp_path))
    main, inset, others = m.choose_stage_cameras(cams, "hand", "head")
    assert (main.note_name, inset and inset.note_name) == ("cam phone", "the primary")
    assert [c.note_name for c in others] == ["cam glasses"]
    # A selector naming nothing here falls back to the primary.
    main, inset, _ = m.choose_stage_cameras(cams, "chest", None)
    assert main.role == "primary" and inset is None
    # The inset never repeats the picture.
    main, inset, _ = m.choose_stage_cameras(cams, "hand", "hand")
    assert main.mount == "hand" and inset is None
    # Role selectors reach an unmounted camera.
    main, inset, _ = m.choose_stage_cameras(cams, "primary", "secondary")
    assert inset is not None and inset.note_name == "cam phone"


def test_fcpxml_puts_the_main_camera_on_the_spine_the_inset_in_its_corner_the_rest_off(
    tmp_path: Path,
) -> None:
    result = _export(
        tmp_path,
        _stage(tmp_path),
        _request(main_camera="hand", inset_camera="head", inset_corner="top-right", inset_size="small"),
    )
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    assets = {a.attrib["id"]: a for a in root.iter("asset")}
    spine_clip = root.find(".//spine/asset-clip")
    assert spine_clip is not None
    main_src = assets[spine_clip.attrib["ref"]].find("media-rep").attrib["src"]
    assert main_src.endswith("stage1_cam_phone_trimmed.mp4")
    connected = spine_clip.findall("asset-clip")
    assert [c.attrib["name"] for c in connected] == ["Primary", "Cam glasses"]
    inset, other = connected
    assert "enabled" not in inset.attrib and inset.find("adjust-transform") is not None
    assert other.attrib.get("enabled") == "0"


def test_old_pip_corners_becomes_one_inset_bottom_left(tmp_path: Path) -> None:
    request = _request(pip_layout="pip-corners")
    assert (request.inset_camera, request.inset_corner) == ("secondary", "bottom-left")
    result = _export(tmp_path, _stage(tmp_path), request)
    connected = (
        ET.fromstring(result.fcpxml_path.read_bytes()).find(".//spine/asset-clip").findall("asset-clip")
    )
    assert [c.attrib.get("enabled", "1") for c in connected] == ["1", "0"]


def test_overlay_is_dropped_and_said_when_the_main_cameras_trim_starts_off_the_primarys(
    tmp_path: Path,
) -> None:
    result = _export(tmp_path, _stage(tmp_path, phone_offset=3.2, overlay=True), _request(main_camera="hand"))
    assert any("overlay dropped" in a and "cam phone" in a for a in result.anomalies)
    # Same start: the overlay rides along.
    (tmp_path / "b").mkdir()
    clean = _export(tmp_path / "b", _stage(tmp_path / "b", overlay=True), _request(main_camera="hand"))
    assert not any("overlay dropped" in a for a in clean.anomalies)


def test_a_missing_main_camera_trim_falls_back_to_the_primary(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    stage.secondaries[0].trimmed_path.unlink()
    result = _export(tmp_path, stage, _request(main_camera="hand"))
    assert any("cam phone trim missing" in a and "primary is the picture" in a for a in result.anomalies)
    root = ET.fromstring(result.fcpxml_path.read_bytes())
    assets = {a.attrib["id"]: a for a in root.iter("asset")}
    ref = root.find(".//spine/asset-clip").attrib["ref"]
    assert assets[ref].find("media-rep").attrib["src"].endswith("stage1_trimmed.mp4")
