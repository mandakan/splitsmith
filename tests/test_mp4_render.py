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


# --- primary audio from an unseeked read ------------------------------------


def test_stage_audio_is_cut_by_atrim_from_an_unseeked_input(tmp_path: Path) -> None:
    """Input-side ``-ss`` mis-cuts a stream-copied trim's audio (measured
    0.42 s late on a real match), so the audio comes from a second,
    unseeked read of the primary and ``atrim`` cuts it on decoded
    timestamps. The video path keeps its seek."""
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", head_pad=3.0, tail_pad=3.0)
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4")
    inputs = [(i, cmd[i + 1]) for i, a in enumerate(cmd) if a == "-i"]
    # The primary appears twice: seeked first (video), unseeked last (audio).
    assert [p for _, p in inputs] == [str(stage.video_path), str(stage.video_path)]
    first_i, last_i = inputs[0][0], inputs[1][0]
    assert list(cmd[first_i - 4 : first_i]) == ["-ss", "2", "-t", f"{plan.effective_seconds:g}"]
    assert cmd[last_i - 1] not in ("-ss", "-t")
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert f"[1:a]atrim=start=2:duration={plan.effective_seconds:g},asetpts=PTS-STARTPTS[aout]" in fg
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[aout]"]
    assert "0:a?" not in cmd


def test_audio_input_is_last_after_cams_overlay_and_lower_third(tmp_path: Path) -> None:
    secondary = _make_video(tmp_path, "cam.mp4")
    sec = SecondaryClip(video_path=secondary, video=_meta_30fps(), beep_offset_seconds=5.0, label="Cam")
    overlay = _make_video(tmp_path, "overlay.mov")
    stage = _basic_stage(
        tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,), overlay_path=overlay
    )
    comp, plan = _build_plan(stage)
    card = composition.TitleCard(text="Stage 1", duration_seconds=2.0, style="lower-third")
    cmd = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "s.mp4",
        lower_third=mp4_render._LowerThirdInput(path=tmp_path / "lt.png", card=card),
    )
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert inputs == [
        str(stage.video_path),
        str(secondary),
        str(overlay),
        str(tmp_path / "lt.png"),
        str(stage.video_path),
    ]
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert "[4:a]atrim=" in fg
    assert "[3:v]format=rgba" in fg


def test_a_primary_without_audio_maps_none(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    cmd = mp4_render._build_stage_command(
        plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4", primary_audio=False
    )
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"] == [str(stage.video_path)]
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["[final]"]
    assert "atrim" not in cmd[cmd.index("-filter_complex") + 1]


def test_has_audio_stream_is_tolerant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a probe that ran and found no audio says False."""
    import subprocess as sp

    calls: list[str] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(cmd[-1]))
        if "silent" in str(cmd[-1]):
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "missing" in str(cmd[-1]):
            raise sp.CalledProcessError(1, cmd, stderr="No such file")
        return sp.CompletedProcess(cmd, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(mp4_render.subprocess, "run", fake_run)
    assert mp4_render._has_audio_stream(Path("/x/with-audio.mp4")) is True
    assert mp4_render._has_audio_stream(Path("/x/silent.mp4")) is False
    assert mp4_render._has_audio_stream(Path("/x/missing.mp4")) is True


@pytest.mark.integration
def test_stage_audio_stays_on_the_video_across_a_keyframe_misaligned_trim(tmp_path: Path) -> None:
    """The defect as it happened: a stream-copied trim whose cut fell
    between keyframes, so the video starts on the earlier keyframe and an
    edit list realigns the audio. Rendering two such stages through the
    real ffmpeg, the beep tone in the audio must land where the
    composition says it does in both -- input-side seeking put stage 2's
    0.42 s early on the real match, and every later stage with it."""
    import shutil

    import numpy as np

    from splitsmith.fcpxml_gen import probe_video
    from tests.synthetic_media import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    # 24 s source, 1 s GOP, a 1 kHz tone burst at 10.0-10.2 s on silence.
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=24",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=24",
            "-filter_complex", "[1:a]volume='between(t,10,10.2)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
            "-g", "25", "-keyint_min", "25", "-sc_threshold", "0", "-c:a", "aac", "-shortest", str(source),
        ],  # fmt: skip
        check=True,
        capture_output=True,
    )
    # Stream-copy trims starting between keyframes: 3.42 s and 3.0 s in.
    trims = []
    for i, start in enumerate((3.42, 3.0)):
        trim = tmp_path / f"trim{i}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-ss",
                f"{start}",
                "-i",
                str(source),
                "-t",
                "12",
                "-c",
                "copy",
                str(trim),
            ],
            check=True,
            capture_output=True,
        )
        trims.append((trim, start))
    # Each trim's beep is the tone at source 10.0 s: clip-local 10 - start.
    stages = [
        StageComposition(
            stage_name=f"S{i}",
            video_path=trim,
            video=probe_video(trim),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=10.0 - start,
            head_pad_seconds=2.0,
            tail_pad_seconds=2.0,
        )
        for i, (trim, start) in enumerate(trims)
    ]
    comp = composition.from_stage_compositions(stages, project_name="m")
    out = tmp_path / "m.mp4"
    result = mp4_render.render_mp4(comp, output_path=out, work_dir=tmp_path / "work", ffmpeg_binary=ffmpeg)
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(out), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    env = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32))
    # Per stage the tone should start 2.0 s (the head pad) into the stage.
    plan = mp4_render.plan_timeline(comp)
    cursor = 0.0
    for item in plan.items:
        expected = cursor + 2.0
        window = env[int((expected - 1.0) * 8000) : int((expected + 1.0) * 8000)]
        onset = (expected - 1.0) + int(np.argmax(window > window.max() * 0.5)) / 8000
        assert (
            abs(onset - expected)
            < 0.1  # detector slop on an AAC tone is a few 21 ms frames; the defect was 0.42 s
        ), f"{item.kind} {item.index}: tone at {onset:.3f}, expected {expected:.3f}"
        cursor += item.duration_seconds
    assert result.duration_seconds == pytest.approx(cursor)


# --- chapter atoms (#204 follow-up) ----------------------------------------


class _Mark:
    def __init__(self, start_seconds: float, title: str) -> None:
        self.start_seconds = start_seconds
        self.title = title


def test_chapter_metadata_chains_ends_and_escapes_titles() -> None:
    text = mp4_render.chapter_metadata(
        [_Mark(0.0, "Match"), _Mark(5.0, "Stage 1; A=B #2"), _Mark(12.345, "Stage\\2")],
        total_seconds=20.0,
    )
    assert text.startswith(";FFMETADATA1\n")
    blocks = text.split("[CHAPTER]")[1:]
    assert len(blocks) == 3
    assert "TIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=Match" in blocks[0]
    assert "START=5000\nEND=12345\ntitle=Stage 1\\; A\\=B \\#2" in blocks[1]
    assert "START=12345\nEND=20000\ntitle=Stage\\\\2" in blocks[2]


def test_chapter_metadata_drops_marks_the_timeline_cannot_hold() -> None:
    # Out of order in, ordered out; a mark at the end, past it, or on top
    # of the previous one would be a zero-length or reversed chapter.
    text = mp4_render.chapter_metadata(
        [_Mark(8.0, "B"), _Mark(0.0, "A"), _Mark(8.0, "B again"), _Mark(20.0, "end"), _Mark(25.0, "past")],
        total_seconds=20.0,
    )
    blocks = text.split("[CHAPTER]")[1:]
    assert [b.split("title=")[1].strip() for b in blocks] == ["A", "B"]
    assert "START=0\nEND=8000" in blocks[0]
    assert "START=8000\nEND=20000" in blocks[1]


def test_chapters_ride_the_stitch_as_a_metadata_input_and_leave_a_plain_render_alone(tmp_path: Path) -> None:
    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="m")

    plain = MagicMock(side_effect=_ok)
    mp4_render.render_mp4(comp, output_path=tmp_path / "plain.mp4", work_dir=tmp_path / "w1", runner=plain)
    plain_concat = plain.call_args_list[-1].args[0]
    assert "-map_metadata" not in plain_concat
    assert not (tmp_path / "w1" / "chapters.ffmeta").exists()

    marks = [_Mark(0.0, "A"), _Mark(float(mp4_render.plan_timeline(comp).items[0].duration_seconds), "B")]
    chaptered = MagicMock(side_effect=_ok)
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "c.mp4", work_dir=tmp_path / "w2", runner=chaptered, chapters=marks
    )
    concat = chaptered.call_args_list[-1].args[0]
    meta = tmp_path / "w2" / "chapters.ffmeta"
    assert meta.exists()
    i = concat.index("-map_metadata")
    assert list(concat[i - 2 : i + 4]) == ["-i", str(meta), "-map_metadata", "1", "-map", "0"]
    # The metadata input sits after the concat input and before the codecs.
    assert concat.index(str(meta)) > concat.index("concat")
    assert concat.index("-map") < concat.index("-c")
    # The last chapter ends where the stitched timeline does.
    assert f"END={int(round(result.duration_seconds * 1000))}" in meta.read_text()
    # Everything but the chapter slice is the plain argv.
    stripped = list(concat[: i - 2]) + list(concat[i + 4 :])
    assert [a for a in stripped if not a.endswith(".mp4") and "concat.txt" not in a] == [
        a for a in plain_concat if not a.endswith(".mp4") and "concat.txt" not in a
    ]


@pytest.mark.integration
def test_rendered_mp4_carries_the_chapter_atoms(tmp_path: Path) -> None:
    """ffprobe reads the chapters back out of a real stitch, at the
    timeline's own stage starts, titles intact."""
    import json
    import shutil

    from splitsmith.fcpxml_gen import probe_video
    from tests.synthetic_media import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg is not None and ffprobe is not None
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=8",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source),
        ],  # fmt: skip
        check=True,
        capture_output=True,
    )
    stages = [
        StageComposition(
            stage_name=name,
            video_path=source,
            video=probe_video(source),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=2.0,
            head_pad_seconds=1.0,
            tail_pad_seconds=1.0,
        )
        for name in ("Skolhuset", "Parkeringen; B=2")
    ]
    comp = composition.from_stage_compositions(stages, project_name="m")
    plan = mp4_render.plan_timeline(comp)
    marks = [_Mark(0.0, "Skolhuset"), _Mark(plan.items[0].duration_seconds, "Parkeringen; B=2")]
    out = tmp_path / "m.mp4"
    result = mp4_render.render_mp4(
        comp, output_path=out, work_dir=tmp_path / "work", ffmpeg_binary=ffmpeg, chapters=marks
    )
    probe = json.loads(
        subprocess.run(
            [ffprobe, "-v", "error", "-show_chapters", "-of", "json", str(out)],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )
    chapters = probe["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["Skolhuset", "Parkeringen; B=2"]
    assert float(chapters[0]["start_time"]) == pytest.approx(0.0, abs=0.001)
    assert float(chapters[1]["start_time"]) == pytest.approx(plan.items[0].duration_seconds, abs=0.001)
    assert float(chapters[1]["end_time"]) == pytest.approx(result.duration_seconds, abs=0.001)
