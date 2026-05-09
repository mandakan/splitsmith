"""Tests for the FCPXML emitter in compare/emitter.py.

Mirrors the unit-test pattern from ``tests/test_fcpxml_gen.py``: synthetic
``VideoMetadata``, empty placeholder mp4s, ffmpeg + ffprobe stubbed via
runner injection. Asserts the emitted XML's shape rather than driving real
FCP.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from splitsmith.compare.emitter import emit_compare_fcpxml
from splitsmith.compare.manifest import CompareManifest, CompareShooter
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.config import OutputConfig
from splitsmith.ui.project import MatchProject
from tests._dtd import fcpxml_dtd, fcpxml_dtd_path, validate_against_dtd


def _stub_ffmpeg_runner() -> Any:
    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return stub


def _stage(
    *,
    stage_number: int,
    stage_name: str,
    trim_path: Path,
    beep: float = 5.0,
    duration: float = 30.0,
    width: int = 1920,
    height: int = 1080,
    fps_num: int = 30,
    fps_den: int = 1,
) -> CompareStageBundle:
    trim_path.parent.mkdir(parents=True, exist_ok=True)
    if not trim_path.exists():
        trim_path.write_bytes(b"")
    return CompareStageBundle(
        stage_number=stage_number,
        stage_name=stage_name,
        trim_path=trim_path,
        audit_path=trim_path.with_suffix(".json"),
        beep_offset_in_clip=beep,
        duration_seconds=duration,
        width=width,
        height=height,
        frame_rate_num=fps_num,
        frame_rate_den=fps_den,
    )


def _bundle(
    label: str,
    stages: list[CompareStageBundle],
    *,
    project_root: Path,
    project_name: str = "p",
) -> CompareShooterBundle:
    proj = MatchProject.init(project_root, name=project_name)
    return CompareShooterBundle(
        label=label,
        project_root=project_root,
        project=proj,
        stages_by_number={s.stage_number: s for s in stages},
    )


def _manifest(
    output: Path,
    audio_from: str,
    labels: list[str],
    layout_2up: str = "horizontal",
) -> CompareManifest:
    return CompareManifest(
        output=output,
        audio_from=audio_from,
        layout_2up=layout_2up,
        shooters=[CompareShooter(project=Path(f"/p/{lab}"), label=lab) for lab in labels],
    )


def test_two_shooters_minimal_structure(tmp_path: Path) -> None:
    a_root = tmp_path / "a"
    b_root = tmp_path / "b"
    a = _bundle(
        "Anders",
        [
            _stage(
                stage_number=1,
                stage_name="Skipper",
                trim_path=a_root / "exports" / "stage1_a.mp4",
            )
        ],
        project_root=a_root,
    )
    b = _bundle(
        "Mathias",
        [
            _stage(
                stage_number=1,
                stage_name="Skipper",
                trim_path=b_root / "exports" / "stage1_b.mp4",
            )
        ],
        project_root=b_root,
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(
        manifest=m,
        shooters=[a, b],
        output_path=out,
        config=OutputConfig(),
        runner=_stub_ffmpeg_runner(),
    )
    assert out.exists()
    content = out.read_bytes()
    assert content.startswith(b"<?xml")
    assert b"<!DOCTYPE fcpxml>" in content
    root = ET.fromstring(content)
    assert root.tag == "fcpxml"
    # Sequence format set from the audio-source shooter.
    fmt = root.find("./resources/format")
    assert fmt is not None
    assert fmt.attrib["frameDuration"] == "1/30s"
    assert fmt.attrib["width"] == "1920"
    # One <media> compound clip per included stage.
    media = root.findall("./resources/media")
    assert len(media) == 1
    # Outer spine has one <ref-clip> per stage.
    ref_clips = root.findall("./library/event/project/sequence/spine/ref-clip")
    assert len(ref_clips) == 1
    assert "Stage 1" in ref_clips[0].attrib["name"]
    # And each ref-clip has a marker for the stage start.
    markers = ref_clips[0].findall("marker")
    assert len(markers) == 1
    assert "Skipper" in markers[0].attrib["value"]


def test_audio_routing_only_audio_from_unmuted(tmp_path: Path) -> None:
    a = _bundle(
        "Anders",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "a" / "x.mp4")],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "Mathias",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "b" / "x.mp4")],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(
        manifest=m,
        shooters=[a, b],
        output_path=out,
        runner=_stub_ffmpeg_runner(),
    )
    root = ET.fromstring(out.read_bytes())
    media = root.find("./resources/media")
    assert media is not None
    clips = media.findall("./sequence/spine/asset-clip")
    assert len(clips) == 2
    by_name = {c.attrib["name"]: c for c in clips}
    # Anders is muted; Mathias is live.
    anders_vol = by_name["Anders"].find("adjust-volume")
    mathias_vol = by_name["Mathias"].find("adjust-volume")
    assert anders_vol is not None and anders_vol.attrib["amount"] == "-96dB"
    assert mathias_vol is None


def test_alphabetical_first_present_is_spine_clip(tmp_path: Path) -> None:
    a = _bundle(
        "Anders",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "a" / "x.mp4")],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "Mathias",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "b" / "x.mp4")],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    root = ET.fromstring(out.read_bytes())
    spine_clips = root.findall("./resources/media/sequence/spine/asset-clip")
    # Anders (alphabetically first) is the spine clip with no lane attr.
    spine_first = spine_clips[0]
    assert spine_first.attrib["name"] == "Anders"
    assert "lane" not in spine_first.attrib
    # Mathias rides on lane 1.
    assert spine_clips[1].attrib["name"] == "Mathias"
    assert spine_clips[1].attrib["lane"] == "1"


def test_beep_alignment_offsets_smaller_beep_later(tmp_path: Path) -> None:
    # Anders beep at 4.0s, Mathias at 5.1s. Mathias has the larger beep
    # so it lands at parent t=0; Anders is offset by ~33 frames @ 30fps.
    a = _bundle(
        "Anders",
        [
            _stage(
                stage_number=1,
                stage_name="X",
                trim_path=tmp_path / "a" / "x.mp4",
                beep=4.0,
            )
        ],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "Mathias",
        [
            _stage(
                stage_number=1,
                stage_name="X",
                trim_path=tmp_path / "b" / "x.mp4",
                beep=5.1,
            )
        ],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    root = ET.fromstring(out.read_bytes())
    by_name = {
        c.attrib["name"]: c for c in root.findall("./resources/media/sequence/spine/asset-clip")
    }
    # delta = round((5.1 - 4.0)/0.0333...) = 33
    assert by_name["Anders"].attrib["offset"] == "33/30s"
    assert by_name["Anders"].attrib["start"] == "0s"
    assert by_name["Mathias"].attrib["offset"] == "0s"
    assert by_name["Mathias"].attrib["start"] == "0s"


def test_layout_2up_vertical_position_x_zero(tmp_path: Path) -> None:
    a = _bundle(
        "A",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "a" / "x.mp4")],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "B",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "b" / "x.mp4")],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "A", ["A", "B"], layout_2up="vertical")
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    root = ET.fromstring(out.read_bytes())
    transforms = root.findall("./resources/media/sequence/spine/asset-clip/adjust-transform")
    # vertical 2up: x=0 for both tiles
    for t in transforms:
        x_str, _y_str = t.attrib["position"].split()
        assert float(x_str) == 0.0


def test_filler_emitted_for_missing_tile(tmp_path: Path) -> None:
    # Roster of 3, only 2 present in stage 1: one filler tile expected.
    a = _bundle(
        "A",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "a" / "x.mp4")],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "B",
        [_stage(stage_number=1, stage_name="X", trim_path=tmp_path / "b" / "x.mp4")],
        project_root=tmp_path / "b",
    )
    # C has no stage 1 trim
    c = _bundle("C", [], project_root=tmp_path / "c")
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "A", ["A", "B", "C"])

    runner_calls: list[list[str]] = []

    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        runner_calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    emit_compare_fcpxml(manifest=m, shooters=[a, b, c], output_path=out, runner=stub)
    root = ET.fromstring(out.read_bytes())
    clips = root.findall("./resources/media/sequence/spine/asset-clip")
    names = [c.attrib["name"] for c in clips]
    assert "A" in names and "B" in names
    # Filler clips are named "filler" by the emitter; assert one is present
    # for the missing C slot (and possibly an extra unused-cell slot).
    assert names.count("filler") >= 1
    # ffmpeg invoked at least once for the filler.
    assert any("lavfi" in c for c in runner_calls)
    # Filler's underlying asset is named with the deterministic prefix.
    asset_names = [a.attrib["name"] for a in root.findall("./resources/asset")]
    assert any(n.startswith("_compare_filler_") for n in asset_names)


def test_stage_present_only_in_secondary_uses_first_present_name(tmp_path: Path) -> None:
    # Audio-source shooter (Mathias) has no stage 2, but Anders does.
    a = _bundle(
        "Anders",
        [
            _stage(stage_number=1, stage_name="One", trim_path=tmp_path / "a" / "1.mp4"),
            _stage(
                stage_number=2,
                stage_name="Anders Stage Two",
                trim_path=tmp_path / "a" / "2.mp4",
            ),
        ],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "Mathias",
        [_stage(stage_number=1, stage_name="One", trim_path=tmp_path / "b" / "1.mp4")],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    root = ET.fromstring(out.read_bytes())
    ref_clips = root.findall("./library/event/project/sequence/spine/ref-clip")
    assert len(ref_clips) == 2
    assert ref_clips[0].attrib["name"] == "Stage 1 -- One"
    assert ref_clips[1].attrib["name"] == "Stage 2 -- Anders Stage Two"


def test_mixed_frame_rate_allocates_separate_format(tmp_path: Path) -> None:
    a = _bundle(
        "A",
        [
            _stage(
                stage_number=1,
                stage_name="X",
                trim_path=tmp_path / "a" / "x.mp4",
                fps_num=30,
                fps_den=1,
            )
        ],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "B",
        [
            _stage(
                stage_number=1,
                stage_name="X",
                trim_path=tmp_path / "b" / "x.mp4",
                fps_num=30000,
                fps_den=1001,
                width=3840,
                height=2160,
            )
        ],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "A", ["A", "B"])
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    root = ET.fromstring(out.read_bytes())
    formats = root.findall("./resources/format")
    # Sequence format (A's 30fps 1080p) + B's 29.97 4K format.
    assert len(formats) == 2
    rates = sorted(f.attrib["frameDuration"] for f in formats)
    assert rates == sorted(["1/30s", "1001/30000s"])


def test_audio_from_not_among_shooters_raises(tmp_path: Path) -> None:
    out = tmp_path / "out.fcpxml"
    # Build a manifest whose audio_from doesn't match the loaded bundles.
    # CompareManifest's own validator catches the YAML case; the emitter
    # guards against the in-process programming-error case where the
    # caller passed mismatched bundles.
    manifest = CompareManifest(
        output=out,
        audio_from="A",
        shooters=[CompareShooter(project=Path("/p/a"), label="A")],
    )
    with pytest.raises(ValueError, match="audio_from"):
        emit_compare_fcpxml(
            manifest=manifest,
            shooters=[_bundle("Different", [], project_root=tmp_path / "d")],
            output_path=out,
            runner=_stub_ffmpeg_runner(),
        )


def test_audio_from_shooter_with_no_stages_raises(tmp_path: Path) -> None:
    audio_bundle = _bundle("A", [], project_root=tmp_path / "a")
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "A", ["A"])
    with pytest.raises(ValueError, match="no stages"):
        emit_compare_fcpxml(
            manifest=m,
            shooters=[audio_bundle],
            output_path=out,
            runner=_stub_ffmpeg_runner(),
        )


@fcpxml_dtd
def test_emitted_fcpxml_validates_against_dtd(tmp_path: Path) -> None:
    a = _bundle(
        "Anders",
        [_stage(stage_number=1, stage_name="Skipper", trim_path=tmp_path / "a" / "1.mp4")],
        project_root=tmp_path / "a",
    )
    b = _bundle(
        "Mathias",
        [_stage(stage_number=1, stage_name="Skipper", trim_path=tmp_path / "b" / "1.mp4")],
        project_root=tmp_path / "b",
    )
    out = tmp_path / "out.fcpxml"
    m = _manifest(out, "Mathias", ["Anders", "Mathias"])
    emit_compare_fcpxml(manifest=m, shooters=[a, b], output_path=out, runner=_stub_ffmpeg_runner())
    validate_against_dtd(out, dtd=fcpxml_dtd_path())
