"""Tests for the ffmpeg MP4 renderer (issue #174).

Command construction is split into pure functions so tests can assert
against the args list without shelling out. The renderer-level tests
mock ``subprocess`` and verify the per-stage and concat invocations
fire in the expected order; manual ffmpeg execution against real media
is the release gate (and lives behind ``@pytest.mark.integration`` for
when we wire it in).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from splitsmith import composition, mp4_render
from splitsmith.config import Shot, VideoMetadata
from splitsmith.fcpxml_gen import (
    PipPlacement,
    SecondaryClip,
    StageComposition,
)


def _shot(n: int, t: float, s: float) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + t,
        time_from_beep=t,
        split=s,
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


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def _basic_stage(
    *,
    tmp_path: Path,
    name: str,
    primary_name: str,
    secondaries: tuple[SecondaryClip, ...] = (),
    overlay_path: Path | None = None,
    head_pad: float = 5.0,
    tail_pad: float = 14.0,
) -> StageComposition:
    """Default pads keep the full clip (head_pad >= head_avail, tail_pad >=
    tail_avail) so the alignment math is decoupled from trim math --
    individual tests override pads when they want to exercise trimming."""
    return StageComposition(
        stage_name=name,
        video_path=_make_video(tmp_path, primary_name),
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
        beep_offset_seconds=5.0,
        head_pad_seconds=head_pad,
        tail_pad_seconds=tail_pad,
        secondaries=secondaries,
        overlay_path=overlay_path,
        overlay_video=_meta_30fps() if overlay_path else None,
    )


def _build_plan(stage: StageComposition) -> tuple[Any, Any]:
    """Return (Composition, _StagePlan) for a single-stage input."""
    comp = composition.from_stage_compositions([stage], project_name="m")
    plan = mp4_render._plan_stage(comp.stages[0], comp.sequence)
    return comp, plan


# --- planning -------------------------------------------------------------


def test_plan_stage_computes_trim_and_alignment(tmp_path: Path) -> None:
    """Default-helper pads keep the full clip; cam beep 5.0 == primary
    beep so delta=0 -> spine_start=0, seek=0."""
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(
        video_path=secondary,
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Cam",
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,))
    _, plan = _build_plan(stage)
    assert plan.head_trim_seconds == pytest.approx(0.0)
    assert plan.effective_seconds == pytest.approx(20.0)
    assert len(plan.cam_alignments) == 1
    align = plan.cam_alignments[0]
    assert align.cam_seek_seconds == pytest.approx(0.0)
    assert align.cam_spine_start == pytest.approx(0.0)


def test_plan_stage_trims_head_when_pad_smaller_than_avail(tmp_path: Path) -> None:
    """head_pad=2 on a clip with 5s before the beep -> head_trim=3s.
    Effective = 20 - 3 - tail_trim. Last shot at clip-local 6.3s,
    tail_avail=13.7s, tail_pad=2 -> tail_trim=11.7. Effective=5.3."""
    stage = _basic_stage(
        tmp_path=tmp_path,
        name="A",
        primary_name="a.mp4",
        head_pad=2.0,
        tail_pad=2.0,
    )
    _, plan = _build_plan(stage)
    assert plan.head_trim_seconds == pytest.approx(3.0)
    assert plan.effective_seconds == pytest.approx(5.3)


def test_plan_stage_cam_late_uses_spine_offset(tmp_path: Path) -> None:
    """Cam beep at 2.0s (3s earlier than primary's at 5.0s) -> cam needs
    to appear 3s into the spine; ffmpeg seeks 0 into the cam."""
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(
        video_path=secondary,
        video=_meta_30fps(),
        beep_offset_seconds=2.0,
        label="Cam",
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,))
    _, plan = _build_plan(stage)
    align = plan.cam_alignments[0]
    assert align.cam_seek_seconds == pytest.approx(0.0)
    assert align.cam_spine_start == pytest.approx(3.0)


def test_plan_stage_cam_early_seeks_into_source(tmp_path: Path) -> None:
    """Cam beep at 8.0s (3s later than primary's at 5.0s) -> cam appears
    at spine 0 but ffmpeg seeks 3s into the cam media so beeps line up."""
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(
        video_path=secondary,
        video=_meta_30fps(),
        beep_offset_seconds=8.0,
        label="Cam",
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,))
    _, plan = _build_plan(stage)
    align = plan.cam_alignments[0]
    assert align.cam_seek_seconds == pytest.approx(3.0)
    assert align.cam_spine_start == pytest.approx(0.0)


def test_plan_stage_negative_effective_raises(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "tiny.mp4")
    tiny = VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=0.001,
        frame_rate_num=30,
        frame_rate_den=1,
    )
    stage = StageComposition(
        stage_name="x",
        video_path=primary,
        video=tiny,
        shots=[],
        beep_offset_seconds=0.0,
        head_pad_seconds=0.0,
        tail_pad_seconds=0.0,
    )
    comp = composition.from_stage_compositions([stage], project_name="m")
    with pytest.raises(ValueError, match="non-positive effective duration"):
        mp4_render._plan_stage(comp.stages[0], comp.sequence)


# --- stage command --------------------------------------------------------


def test_build_stage_command_minimal(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "stage.mp4")
    # Sanity: the trim window matches the plan; the only video input is
    # the primary; no filter graph branch for cams or overlay.
    assert "-ss" in cmd and "-t" in cmd
    assert str(stage.video_path) in cmd
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v]setpts=PTS-STARTPTS,format=yuv420p[base]" in fg
    # ``[base]null[final]`` is the no-cam-no-overlay terminal node.
    assert "[base]null[final]" in fg


def test_build_stage_command_pip_secondary(tmp_path: Path) -> None:
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(
        video_path=secondary,
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Cam",
        pip=PipPlacement(corner="top-right"),
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,))
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "stage.mp4")
    fg = cmd[cmd.index("-filter_complex") + 1]
    # Cam scaled to 30% of 1920x1080 -> 576x324.
    assert "scale=576:324" in fg
    # ffmpeg overlay X = (1920*(1-0.3))/2 + 633.6 = 672 + 633.6 = 1305.6
    # ffmpeg overlay Y = (1080*(1-0.3))/2 - 356.4 = 378 - 356.4 = 21.6
    assert "overlay=x=1305.6:y=21.6" in fg
    # ``enable`` keeps the cam visible only during its computed window.
    assert "between(t," in fg


def test_build_stage_command_overlay_emits_top_overlay(tmp_path: Path) -> None:
    overlay = _make_video(tmp_path, "overlay.mov")
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", overlay_path=overlay)
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "stage.mp4")
    fg = cmd[cmd.index("-filter_complex") + 1]
    # The overlay input lives at index 1 (no cams) and lands as the
    # final layer at (0,0) with full-frame coverage.
    assert "[1:v]setpts=PTS-STARTPTS[overlay_v]" in fg
    assert "[overlay_v]overlay=0:0[withov]" in fg


def test_build_stage_command_pip_full_frame_when_no_transform(tmp_path: Path) -> None:
    """A cam with no PiP lands at (0, 0) full-frame -- mirrors today's
    stacked layout, just baked into pixels instead of FCP layers."""
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(
        video_path=secondary,
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Cam",
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,))
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "stage.mp4")
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=x=0:y=0" in fg
    # No ``scale=`` filter when the cam runs at native size (default
    # ``Transform.scale`` is 1.0; absence of any transform skips scale
    # entirely).
    assert "scale=" not in fg


# --- concat command -------------------------------------------------------


def test_build_concat_command_stream_copies(tmp_path: Path) -> None:
    cmd = mp4_render._build_concat_command(
        list_path=tmp_path / "list.txt",
        output_path=tmp_path / "out.mp4",
    )
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    # ``-c copy`` is the whole point of the concat step -- per-stage temps
    # were already encoded to compatible codecs, no need to re-encode.
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert "-movflags" in cmd
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"


# --- end-to-end orchestration --------------------------------------------


def _ok(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")


def test_render_mp4_runs_per_stage_then_concat(tmp_path: Path) -> None:
    """The renderer fires N stage invocations then one concat
    invocation, in that order."""
    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="m")

    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    out = tmp_path / "match.mp4"
    mp4_render.render_mp4(comp, output_path=out, work_dir=work, runner=runner)

    assert runner.call_count == 3
    args_per_call = [call.args[0] for call in runner.call_args_list]
    # Two per-stage invocations finish with a stage-N temp path.
    assert args_per_call[0][-1].endswith("stage_000.mp4")
    assert args_per_call[1][-1].endswith("stage_001.mp4")
    # Final invocation is the concat step.
    final = args_per_call[2]
    assert "-f" in final and final[final.index("-f") + 1] == "concat"
    assert final[-1] == str(out)
    # The concat list got written between the per-stage and concat
    # steps, listing both temps in spine order.
    list_path = work / "concat.txt"
    assert list_path.exists()
    contents = list_path.read_text()
    assert "stage_000.mp4" in contents
    assert "stage_001.mp4" in contents
    # Spine order: A before B.
    assert contents.index("stage_000") < contents.index("stage_001")


def test_render_mp4_propagates_ffmpeg_error(tmp_path: Path) -> None:
    """A non-zero ffmpeg exit surfaces as ``FFmpegError`` with the
    captured stderr -- the export endpoint relies on this to bubble a
    helpful message to the dialog."""
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp = composition.from_stage_compositions([stage], project_name="m")

    def fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(returncode=1, cmd=args[0], stderr="boom: invalid argument")

    with pytest.raises(mp4_render.FFmpegError, match="boom"):
        mp4_render.render_mp4(
            comp,
            output_path=tmp_path / "out.mp4",
            work_dir=tmp_path / "work",
            runner=fail,
        )


def test_render_mp4_missing_binary_raises(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp = composition.from_stage_compositions([stage], project_name="m")

    def missing(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(args[0][0])

    with pytest.raises(mp4_render.FFmpegError, match="not found"):
        mp4_render.render_mp4(
            comp,
            output_path=tmp_path / "out.mp4",
            work_dir=tmp_path / "work",
            ffmpeg_binary="ffmpeg-nope",
            runner=missing,
        )


# --- youtube preset (#204 layer 2) ---------------------------------------


def _meta_60fps() -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=20.0,
        frame_rate_num=60,
        frame_rate_den=1,
    )


def test_default_encode_args_match_today(tmp_path: Path) -> None:
    """Without the preset the encode params keep today's CRF 20 / fast /
    AAC 192k profile -- the byte-equivalence guarantee for existing
    consumers."""
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "stage.mp4")
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "20"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "fast"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    # YouTube-specific tags must NOT leak into the default profile.
    assert "-color_primaries" not in cmd
    assert "-profile:v" not in cmd
    assert "-g" not in cmd


def test_youtube_preset_emits_recommended_codec_params_30fps(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "stage.mp4",
        youtube_preset=True,
    )
    # H.264 High @ Level 4.2, CRF 18, slow preset.
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-preset") + 1] == "slow"
    assert cmd[cmd.index("-profile:v") + 1] == "high"
    assert cmd[cmd.index("-level") + 1] == "4.2"
    assert cmd[cmd.index("-crf") + 1] == "18"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    # 2s GOP at 30fps -> 60.
    assert cmd[cmd.index("-g") + 1] == "60"
    assert cmd[cmd.index("-keyint_min") + 1] == "60"
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    # rec.709 colour tags so YouTube doesn't autodetect wrong.
    assert cmd[cmd.index("-color_primaries") + 1] == "bt709"
    assert cmd[cmd.index("-color_trc") + 1] == "bt709"
    assert cmd[cmd.index("-colorspace") + 1] == "bt709"
    # AAC-LC 48k stereo at the upper end of YouTube's recommended range.
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "384k"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-ac") + 1] == "2"
    # +faststart so the moov atom lands at the head -- progressive
    # streaming + faster YouTube ingest.
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"


def test_youtube_preset_doubles_gop_at_60fps(tmp_path: Path) -> None:
    """2s GOP at 60fps -> 120 keyframe interval. Resolution doesn't
    change the GOP -- only the source frame rate does."""
    stage = StageComposition(
        stage_name="A",
        video_path=_make_video(tmp_path, "a.mp4"),
        video=_meta_60fps(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        head_pad_seconds=10.0,
        tail_pad_seconds=20.0,
    )
    comp = composition.from_stage_compositions([stage], project_name="m")
    plan = mp4_render._plan_stage(comp.stages[0], comp.sequence)
    cmd = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "stage.mp4",
        youtube_preset=True,
    )
    assert cmd[cmd.index("-g") + 1] == "120"
    assert cmd[cmd.index("-keyint_min") + 1] == "120"


def test_youtube_preset_threads_through_render_mp4(tmp_path: Path) -> None:
    """Passing ``youtube_preset=True`` to ``render_mp4`` reaches the
    per-stage encode -- per-stage cmd carries CRF 18; the concat step
    stays stream-copy regardless."""
    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="m")

    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    out = tmp_path / "match.mp4"
    mp4_render.render_mp4(
        comp,
        output_path=out,
        work_dir=work,
        runner=runner,
        youtube_preset=True,
    )

    args_per_call = [call.args[0] for call in runner.call_args_list]
    # Both per-stage invocations carry the YouTube codec params.
    for stage_cmd in args_per_call[:2]:
        assert stage_cmd[stage_cmd.index("-crf") + 1] == "18"
        assert stage_cmd[stage_cmd.index("-profile:v") + 1] == "high"
        assert stage_cmd[stage_cmd.index("-color_primaries") + 1] == "bt709"
    # Concat is stream-copy; no codec swap there.
    concat = args_per_call[2]
    assert "-c" in concat and concat[concat.index("-c") + 1] == "copy"
    assert "-crf" not in concat


def test_render_mp4_requires_at_least_one_stage(tmp_path: Path) -> None:
    """Empty stages on a Composition shouldn't even reach ffmpeg."""
    # Build an empty composition by sidestepping the constructor's guard.
    comp = composition.Composition(
        project_name="m",
        sequence=composition.SequenceFormat.from_video(_meta_30fps()),
        stages=(),
    )
    with pytest.raises(ValueError, match="at least one stage"):
        mp4_render.render_mp4(comp, output_path=tmp_path / "out.mp4", work_dir=tmp_path / "work")


def test_render_mp4_rejects_mixed_frame_rates_with_clear_message(
    tmp_path: Path,
) -> None:
    """#233 -- the MP4 renderer can't conform per-asset rates without
    re-encoding through ``fps=``. Surface a clear error naming the
    offending stage and pointing the user at the FCPXML / FCP7
    renderers which do conform."""
    meta_60 = VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=20.0,
        frame_rate_num=60,
        frame_rate_den=1,
    )
    stage_a = StageComposition(
        stage_name="A",
        video_path=_make_video(tmp_path, "a.mp4"),
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        head_pad_seconds=10.0,
        tail_pad_seconds=20.0,
    )
    stage_b = StageComposition(
        stage_name="B",
        video_path=_make_video(tmp_path, "b.mp4"),
        video=meta_60,
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        head_pad_seconds=10.0,
        tail_pad_seconds=20.0,
    )
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="match")
    with pytest.raises(ValueError, match="mp4 renderer requires"):
        mp4_render.render_mp4(comp, output_path=tmp_path / "out.mp4", work_dir=tmp_path / "work")


# --- generated cards, intro / outro (issue #973) ---------------------------


class _FakeRasterizer:
    """Returns a real transparent PNG so the compositing is exercised."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        import io

        from PIL import Image

        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _asset(tmp_path: Path, name: str, *, seconds: float = 4.0) -> composition.Asset:
    meta = _meta_30fps().model_copy(update={"duration_seconds": seconds})
    return composition.Asset(path=_make_video(tmp_path, name), metadata=meta)


def _carded_composition(tmp_path: Path, *, lower_third: bool = False) -> composition.Composition:
    """Two 20 s stages kept whole (pads cover the clip), a 3 s title page,
    1.5 s slates, a 2 s closing card and 4 s intro / outro clips."""
    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    style = "lower-third" if lower_third else "slate"
    titles = {
        0: composition.TitleCard(text="Stage 1", duration_seconds=1.5, style=style, info=("24 rounds",)),
        1: composition.TitleCard(text="Stage 2", duration_seconds=1.5, style=style),
    }
    return composition.from_stage_compositions(
        [stage_a, stage_b],
        project_name="m",
        titles=titles,
        intro=composition.Segment(asset=_asset(tmp_path, "intro.mp4"), name="intro"),
        outro=composition.Segment(asset=_asset(tmp_path, "outro.mp4"), name="outro"),
        title_page=composition.MatchTitle(text="Bromma", info=("2026-05-01",), duration_seconds=3.0),
        closing=composition.MatchTitle(text="Thanks", duration_seconds=2.0),
    )


def test_plan_timeline_orders_the_spine_and_sums_durations(tmp_path: Path) -> None:
    """Intro, title page, (slate, stage) x N, closing, outro; the total is
    footage plus every card and clip -- the figure
    ``MatchExportResult.duration_seconds`` reports."""
    plan = mp4_render.plan_timeline(_carded_composition(tmp_path))
    assert [item.kind for item in plan.items] == [
        "intro",
        "title_page",
        "slate",
        "stage",
        "slate",
        "stage",
        "closing",
        "outro",
    ]
    assert plan.duration_seconds == pytest.approx(4.0 + 3.0 + 1.5 + 20.0 + 1.5 + 20.0 + 2.0 + 4.0)
    assert plan.needs_rasterizer
    assert plan.has_generated_segments


def test_plan_timeline_without_cards_is_stages_only(tmp_path: Path) -> None:
    comp = composition.from_stage_compositions(
        [_basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")], project_name="m"
    )
    plan = mp4_render.plan_timeline(comp)
    assert [item.kind for item in plan.items] == ["stage"]
    assert plan.duration_seconds == pytest.approx(20.0)
    assert not plan.needs_rasterizer
    assert not plan.has_generated_segments


def test_plan_timeline_lower_third_rides_the_stage_not_the_spine(tmp_path: Path) -> None:
    plan = mp4_render.plan_timeline(_carded_composition(tmp_path, lower_third=True))
    kinds = [item.kind for item in plan.items]
    assert "slate" not in kinds
    stages = [item for item in plan.items if item.kind == "stage"]
    assert all(item.lower_third is not None for item in stages)
    # A lower-third overlays the stage's own head: it adds no time.
    assert plan.duration_seconds == pytest.approx(4.0 + 3.0 + 20.0 + 20.0 + 2.0 + 4.0)
    assert plan.needs_rasterizer


def test_build_still_command_loops_the_png_with_silent_audio(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    cmd = mp4_render._build_still_command(
        tmp_path / "card.png", seconds=3.0, sequence=comp.sequence, output_path=tmp_path / "card.mp4"
    )
    assert cmd[cmd.index("-loop") + 1] == "1"
    assert cmd[cmd.index("-framerate") + 1] == "30/1"
    assert any(arg.startswith("anullsrc") for arg in cmd)
    # Output-side ``-t``: both inputs are infinite, the hold bounds them.
    assert cmd[cmd.index("-t") + 1] == "3"
    # Same encode as a stage so the stitch can stream-copy the video.
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "20"
    assert cmd[-1] == str(tmp_path / "card.mp4")


def test_build_segment_command_conforms_the_clip_to_the_sequence(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    assert comp.intro is not None
    cmd = mp4_render._build_segment_command(
        comp.intro, sequence=comp.sequence, output_path=tmp_path / "i.mp4"
    )
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in fg
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2" in fg
    assert "fps=30/1" in fg
    assert "format=yuv420p" in fg
    assert str(comp.intro.asset.path) in cmd


def test_build_stage_command_lower_third_fades_out_over_the_head(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    card = composition.TitleCard(text="Stage 1", duration_seconds=2.0, style="lower-third")
    cmd = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "stage.mp4",
        lower_third=mp4_render._LowerThirdInput(path=tmp_path / "lt.png", card=card),
    )
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert str(tmp_path / "lt.png") in cmd
    # Input 1 (no cams, no overlay) is the PNG; it fades over its last
    # half second and is disabled after ``duration_seconds``.
    assert "[1:v]format=rgba,fade=t=out:st=1.5:d=0.5:alpha=1[lt]" in fg
    assert "overlay=0:0:enable='lt(t,2)'[withlt]" in fg
    assert "[withlt]null[final]" in fg


def test_build_stage_command_without_lower_third_is_unchanged(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    before = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4")
    after = mp4_render._build_stage_command(
        plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4", lower_third=None
    )
    assert before == after


def test_build_concat_command_reencodes_audio_when_asked(tmp_path: Path) -> None:
    """Generated segments carry ``anullsrc`` audio; the trims carry the
    camera's. Re-encoding audio at the stitch is what lets the two
    differ in sample rate, and the video half stays a stream copy."""
    cmd = mp4_render._build_concat_command(
        list_path=tmp_path / "l.txt", output_path=tmp_path / "o.mp4", reencode_audio=True
    )
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-c" not in cmd[: cmd.index("-c:v")]
    plain = mp4_render._build_concat_command(list_path=tmp_path / "l.txt", output_path=tmp_path / "o.mp4")
    assert cmd != plain


def test_render_mp4_splices_cards_and_clips_in_spine_order(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake
    )
    contents = (work / "concat.txt").read_text().splitlines()
    names = [line.rsplit("/", 1)[-1].rstrip("'") for line in contents]
    assert names == [
        "intro.mp4",
        "title_page.mp4",
        "slate_000.mp4",
        "stage_000.mp4",
        "slate_001.mp4",
        "stage_001.mp4",
        "closing.mp4",
        "outro.mp4",
    ]
    # Every card was rasterized once: title page, two slates, closing.
    assert len(fake.calls) == 4
    assert "24 rounds" in fake.calls[1]
    final = runner.call_args_list[-1].args[0]
    assert final[final.index("-c:v") + 1] == "copy"
    assert final[final.index("-c:a") + 1] == "aac"
    assert result.duration_seconds == pytest.approx(56.0)
    assert result.degradations == ()


def test_render_mp4_without_cards_returns_the_stage_duration_and_copies(tmp_path: Path) -> None:
    comp = composition.from_stage_compositions(
        [_basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")], project_name="m"
    )
    runner = MagicMock(side_effect=_ok)
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "m.mp4", work_dir=tmp_path / "w", runner=runner
    )
    assert result.duration_seconds == pytest.approx(20.0)
    final = runner.call_args_list[-1].args[0]
    assert final[final.index("-c") + 1] == "copy"


def test_render_mp4_skips_every_card_when_no_browser_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The degradation path: no usable Chromium records one degradation,
    every card is skipped, the clips and stages still render and stitch,
    and the reported duration is what was actually written."""
    from splitsmith.overlay_raster import RasterizerUnavailableError

    class _NoBrowser:
        def __enter__(self):
            raise RasterizerUnavailableError("no browser", "Chromium could not be launched: boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(mp4_render, "ChromiumRasterizer", _NoBrowser)
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    result = mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner)
    names = [line.rsplit("/", 1)[-1].rstrip("'") for line in (work / "concat.txt").read_text().splitlines()]
    assert names == ["intro.mp4", "stage_000.mp4", "stage_001.mp4", "outro.mp4"]
    assert len(result.degradations) == 1
    assert "boom" in result.degradations[0]
    assert result.duration_seconds == pytest.approx(4.0 + 20.0 + 20.0 + 4.0)


def test_render_mp4_lower_third_needs_no_extra_segment(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path, lower_third=True)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    names = [line.rsplit("/", 1)[-1].rstrip("'") for line in (work / "concat.txt").read_text().splitlines()]
    assert names == [
        "intro.mp4",
        "title_page.mp4",
        "stage_000.mp4",
        "stage_001.mp4",
        "closing.mp4",
        "outro.mp4",
    ]
    # The stage invocations carry the lower-third PNG as an input.
    stage_cmds = [c.args[0] for c in runner.call_args_list if c.args[0][-1].endswith("stage_000.mp4")]
    assert any(str(work / "lower_third_000.png") in cmd for cmd in stage_cmds)


def test_backdrop_grabs_take_the_head_frame_and_the_tail_window(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m.mp4",
        work_dir=tmp_path / "w",
        runner=runner,
        rasterizer=_FakeRasterizer(),
    )
    grabs = [c.args[0] for c in runner.call_args_list if c.args[0][-1].endswith("_backdrop.png")]
    heads = [g for g in grabs if not g[-1].endswith("closing_backdrop.png")]
    (tail,) = [g for g in grabs if g[-1].endswith("closing_backdrop.png")]
    assert len(heads) == 3
    for head in heads:
        assert head[head.index("-frames:v") + 1] == "1" and "-update" not in head
    assert "-update" in tail and tail[tail.index("-t") + 1] == "0.5"


# --- summary hold (issue #972) ---------------------------------------------


def _summarised_composition(tmp_path: Path) -> composition.Composition:
    from splitsmith.match_project import StageScorecard
    from splitsmith.stage_summary_data import TileShot, TileStageData

    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    data = TileStageData(
        label="Me",
        stage_number=1,
        shots=(TileShot(1.0, 1.0), TileShot(1.3, 0.3)),
        stage_time_seconds=4.5,
        scorecard=StageScorecard(hit_factor=12.0, alphas=10),
    )
    hold = composition.SummaryHold(data=data, label="Me", duration_seconds=3.0)
    return composition.from_stage_compositions(
        [stage_a, stage_b], project_name="m", summaries={0: hold, 1: hold}
    )


def test_plan_timeline_puts_a_summary_after_each_stage(tmp_path: Path) -> None:
    plan = mp4_render.plan_timeline(_summarised_composition(tmp_path))
    assert [item.kind for item in plan.items] == ["stage", "summary", "stage", "summary"]
    assert plan.duration_seconds == pytest.approx(20.0 + 3.0 + 20.0 + 3.0)
    assert plan.needs_rasterizer


def test_render_mp4_holds_the_summary_after_the_stage(tmp_path: Path) -> None:
    comp = _summarised_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake
    )
    names = [line.rsplit("/", 1)[-1].rstrip("'") for line in (work / "concat.txt").read_text().splitlines()]
    assert names == ["stage_000.mp4", "summary_000.mp4", "stage_001.mp4", "summary_001.mp4"]
    assert len(fake.calls) == 2
    assert "12.00" in fake.calls[0] and "Me" in fake.calls[0]
    # The summary's backdrop is the stage's last visible frame: a tail grab.
    grabs = [c.args[0] for c in runner.call_args_list if c.args[0][-1].endswith("summary_000_backdrop.png")]
    (grab,) = grabs
    assert "-update" in grab and grab[grab.index("-t") + 1] == "0.5"
    assert result.duration_seconds == pytest.approx(46.0)


def test_summary_without_browser_or_frame_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake runner writes no frame and no browser launches: nothing
    to hold on, so the summary is skipped and the render still stitches."""
    from splitsmith.overlay_raster import RasterizerUnavailableError

    class _NoBrowser:
        def __enter__(self):
            raise RasterizerUnavailableError("no browser", "boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(mp4_render, "ChromiumRasterizer", _NoBrowser)
    comp = _summarised_composition(tmp_path)
    work = tmp_path / "work"
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=MagicMock(side_effect=_ok)
    )
    names = [line.rsplit("/", 1)[-1].rstrip("'") for line in (work / "concat.txt").read_text().splitlines()]
    assert names == ["stage_000.mp4", "stage_001.mp4"]
    assert len(result.degradations) == 1
    assert result.duration_seconds == pytest.approx(40.0)
