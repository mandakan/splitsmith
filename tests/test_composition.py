"""Tests for the composition IR (issue #194).

The IR sits above ``fcpxml_gen.generate_*_fcpxml``; the bridge renderer
(``composition.render_fcpxml``) must produce byte-identical output to the
legacy emitter on every supported input shape so existing user workflows
don't drift. These tests pin both the structural fields and the byte
equivalence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import composition, fcpxml_gen
from splitsmith.config import OutputConfig, Shot, VideoMetadata
from splitsmith.fcpxml_gen import (
    PipPlacement,
    SecondaryClip,
    StageComposition,
    generate_fcpxml,
    generate_match_fcpxml,
)


def _shot(n: int, time_from_beep: float, split: float) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + time_from_beep,
        time_from_beep=time_from_beep,
        split=split,
        peak_amplitude=0.5,
        confidence=0.8,
    )


def _meta_30fps() -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=20.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _meta_2997() -> VideoMetadata:
    return VideoMetadata(
        width=3840,
        height=2160,
        duration_seconds=20.0,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


# --- IR shape --------------------------------------------------------------


def test_sequence_format_from_video_copies_dims_and_rate() -> None:
    seq = composition.SequenceFormat.from_video(_meta_2997())
    assert seq.width == 3840
    assert seq.height == 2160
    assert seq.frame_rate_num == 30000
    assert seq.frame_rate_den == 1001


@pytest.mark.parametrize(
    "corner",
    ["top-right", "top-left", "bottom-right", "bottom-left"],
)
def test_pip_placement_resolve_matches_emitter_attrs(corner: str) -> None:
    """``PipPlacement.resolve`` is the IR's pixel-space source of truth;
    the FCPXML emitter scales those pixels into FCPXML's normalized
    ``100 == sequence_height`` position units (#236). The conversion is
    a fixed scalar for a given sequence height, so both the IR and the
    emitter still agree on placement -- they just speak different units.
    """
    pip = PipPlacement(corner=corner)  # type: ignore[arg-type]
    scale, (x_px, y_px) = pip.resolve(sequence_width=1920, sequence_height=1080)
    attrs = fcpxml_gen._pip_transform_attrs(pip, sequence_width=1920, sequence_height=1080)
    assert attrs["scale"] == f"{scale:g} {scale:g}"
    unit = 100.0 / 1080
    assert attrs["position"] == f"{x_px * unit:g} {y_px * unit:g}"


@pytest.mark.parametrize(
    "corner",
    ["top-right", "top-left", "bottom-right", "bottom-left"],
)
def test_transform_to_pip_round_trips_all_corners(corner: str) -> None:
    """``_transform_to_pip`` must invert ``_pip_to_transform`` for every
    corner so the bridge renderer can lower the IR back to today's
    ``PipPlacement`` without losing information."""
    seq = composition.SequenceFormat(width=1920, height=1080, frame_rate_num=30, frame_rate_den=1)
    pip_in = PipPlacement(corner=corner, scale=0.3, margin_pct=2.5)  # type: ignore[arg-type]
    transform = composition._pip_to_transform(pip_in, seq)
    pip_out = composition._transform_to_pip(transform, seq)
    assert pip_out.corner == pip_in.corner
    assert pip_out.scale == pytest.approx(pip_in.scale)
    assert pip_out.margin_pct == pytest.approx(pip_in.margin_pct)


# --- from_stage_compositions ----------------------------------------------


def test_from_stage_compositions_requires_at_least_one_stage() -> None:
    with pytest.raises(ValueError, match="at least one stage"):
        composition.from_stage_compositions([], project_name="match")


def test_from_stage_compositions_captures_primary_fields(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "primary.mp4")
    stage_comp = StageComposition(
        stage_name="Stage 1",
        video_path=primary,
        video=_meta_30fps(),
        shots=[_shot(1, 0.5, 0.5), _shot(2, 0.7, 0.2)],
        beep_offset_seconds=5.0,
        head_pad_seconds=1.5,
        tail_pad_seconds=2.0,
    )
    comp = composition.from_stage_compositions([stage_comp], project_name="match")
    assert comp.project_name == "match"
    assert comp.sequence.width == 1920
    assert comp.sequence.height == 1080
    assert len(comp.stages) == 1
    stage = comp.stages[0]
    assert stage.name == "Stage 1"
    assert stage.primary.path == primary
    assert stage.primary.metadata == _meta_30fps()
    assert stage.beep_offset_seconds == 5.0
    assert stage.head_pad_seconds == 1.5
    assert stage.tail_pad_seconds == 2.0
    assert len(stage.markers) == 2
    assert stage.markers[0].time_seconds == pytest.approx(5.5)  # beep + 0.5s
    assert stage.markers[0].shot.shot_number == 1
    assert stage.secondaries == ()
    assert stage.overlay is None


def test_from_stage_compositions_resolves_pip_against_sequence_dims(tmp_path: Path) -> None:
    """A secondary's ``PipPlacement`` is high-level (corner / scale / margin);
    the IR stores absolute pixel coordinates so renderers don't each have
    to redo the math."""
    primary = _make_video(tmp_path, "primary.mp4")
    secondary = _make_video(tmp_path, "secondary.mp4")
    stage_comp = StageComposition(
        stage_name="s1",
        video_path=primary,
        video=_meta_30fps(),  # 1920x1080
        shots=[_shot(1, 0.5, 0.5)],
        beep_offset_seconds=5.0,
        head_pad_seconds=5.0,
        tail_pad_seconds=5.0,
        secondaries=(
            SecondaryClip(
                video_path=secondary,
                video=_meta_30fps(),
                beep_offset_seconds=5.0,
                label="Cam",
                pip=PipPlacement(corner="top-right"),
            ),
        ),
    )
    comp = composition.from_stage_compositions([stage_comp], project_name="match")
    sec = comp.stages[0].secondaries[0]
    assert sec.transform is not None
    # 1920x1080 sequence, default scale=0.30, margin_pct=2.0:
    # half_w=960, clip_half_w=288, margin_x=38.4 -> x = 960 - 288 - 38.4 = 633.6
    # half_h=540, clip_half_h=162, margin_y=21.6 -> y = 540 - 162 - 21.6 = 356.4
    assert sec.transform.scale == pytest.approx(0.30)
    assert sec.transform.position[0] == pytest.approx(633.6)
    assert sec.transform.position[1] == pytest.approx(356.4)
    assert sec.beep_offset_seconds == 5.0
    assert sec.role == "cam"


def test_from_stage_compositions_captures_overlay(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "primary.mp4")
    overlay = _make_video(tmp_path, "overlay.mov")
    stage_comp = StageComposition(
        stage_name="s1",
        video_path=primary,
        video=_meta_30fps(),
        shots=[_shot(1, 0.5, 0.5)],
        beep_offset_seconds=5.0,
        head_pad_seconds=5.0,
        tail_pad_seconds=5.0,
        overlay_path=overlay,
        overlay_video=_meta_2997(),  # different geometry from primary
    )
    comp = composition.from_stage_compositions([stage_comp], project_name="match")
    ov = comp.stages[0].overlay
    assert ov is not None
    assert ov.role == "overlay"
    assert ov.asset.path == overlay
    assert ov.asset.metadata == _meta_2997()
    assert ov.beep_offset_seconds is None
    assert ov.transform is None


# --- render_fcpxml byte-equivalence ---------------------------------------


def _read(path: Path) -> bytes:
    return path.read_bytes()


def test_render_fcpxml_single_stage_matches_generate_fcpxml(tmp_path: Path) -> None:
    """A single-stage composition with no intro/outro must emit the same
    bytes as the legacy ``generate_fcpxml`` for an equivalent input."""
    video = _make_video(tmp_path, "v.mp4")
    legacy_out = tmp_path / "legacy.fcpxml"
    ir_out = tmp_path / "ir.fcpxml"

    shots = [_shot(1, 1.42, 1.42), _shot(2, 1.63, 0.21)]
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=legacy_out,
        project_name="v",
        config=OutputConfig(),
    )

    stage_comp = StageComposition(
        stage_name="v",  # bridge uses project_name as the asset-clip name
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        head_pad_seconds=999.0,  # large pads -> no shrink applied at IR layer
        tail_pad_seconds=999.0,
    )
    comp = composition.from_stage_compositions([stage_comp], project_name="v")
    composition.render_fcpxml(comp, output_path=ir_out, config=OutputConfig())

    # Single-stage path uses generate_fcpxml directly so the bytes match.
    assert _read(legacy_out) == _read(ir_out)


def test_render_fcpxml_match_path_matches_generate_match_fcpxml(tmp_path: Path) -> None:
    """Multi-stage composition emits via ``generate_match_fcpxml`` -- the
    bridge must produce identical bytes."""
    primary_a = _make_video(tmp_path, "stage_a.mp4")
    primary_b = _make_video(tmp_path, "stage_b.mp4")
    legacy_out = tmp_path / "legacy.fcpxml"
    ir_out = tmp_path / "ir.fcpxml"

    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
            beep_offset_seconds=5.0,
            head_pad_seconds=2.0,
            tail_pad_seconds=2.0,
        ),
        StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=2.0,
            tail_pad_seconds=2.0,
        ),
    ]
    generate_match_fcpxml(stages=stages, output_path=legacy_out, project_name="match", config=OutputConfig())

    comp = composition.from_stage_compositions(stages, project_name="match")
    composition.render_fcpxml(comp, output_path=ir_out, config=OutputConfig())

    assert _read(legacy_out) == _read(ir_out)


def test_render_fcpxml_match_with_pip_secondaries_matches(tmp_path: Path) -> None:
    """Round-trip with PiP secondaries: IR -> StageComposition recovers the
    same ``PipPlacement`` and emits identical bytes."""
    primary_a = _make_video(tmp_path, "stage_a.mp4")
    secondary_a = _make_video(tmp_path, "cam_a.mp4")
    primary_b = _make_video(tmp_path, "stage_b.mp4")
    secondary_b = _make_video(tmp_path, "cam_b.mp4")
    legacy_out = tmp_path / "legacy.fcpxml"
    ir_out = tmp_path / "ir.fcpxml"

    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary_a,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam A",
                    pip=PipPlacement(corner="top-right"),
                ),
            ),
        ),
        StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary_b,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam B",
                    pip=PipPlacement(corner="bottom-left", scale=0.3, margin_pct=2.0),
                ),
            ),
        ),
    ]
    generate_match_fcpxml(stages=stages, output_path=legacy_out, project_name="match", config=OutputConfig())

    comp = composition.from_stage_compositions(stages, project_name="match")
    composition.render_fcpxml(comp, output_path=ir_out, config=OutputConfig())

    assert _read(legacy_out) == _read(ir_out)


def test_render_fcpxml_match_with_overlay_matches(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "stage.mp4")
    overlay = _make_video(tmp_path, "overlay.mov")
    legacy_out = tmp_path / "legacy.fcpxml"
    ir_out = tmp_path / "ir.fcpxml"

    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            overlay_path=overlay,
            overlay_video=_meta_30fps(),
        ),
        StageComposition(
            stage_name="B",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]
    generate_match_fcpxml(stages=stages, output_path=legacy_out, project_name="match", config=OutputConfig())

    comp = composition.from_stage_compositions(stages, project_name="match")
    composition.render_fcpxml(comp, output_path=ir_out, config=OutputConfig())

    assert _read(legacy_out) == _read(ir_out)


def test_to_stage_compositions_recovers_secondaries_without_pip(tmp_path: Path) -> None:
    """A cam without a transform on the IR side lowers back to a
    SecondaryClip with ``pip=None`` -- preserving the "stacked" layout."""
    primary = _make_video(tmp_path, "primary.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="s1",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam",
                ),
            ),
        )
    ]
    comp = composition.from_stage_compositions(stages, project_name="match")
    lowered = composition.to_stage_compositions(comp)
    assert len(lowered) == 1
    sec = lowered[0].secondaries[0]
    assert sec.pip is None
