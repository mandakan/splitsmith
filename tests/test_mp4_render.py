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


def test_an_angle_carried_for_editing_is_left_out_of_the_mp4(tmp_path: Path) -> None:
    """A cam the export carries switched off (an "other angle" for the
    FCPXML editor) never reaches the MP4: it would cover the picture."""
    shown = SecondaryClip(
        video_path=_make_video(tmp_path, "inset.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Inset",
    )
    off = SecondaryClip(
        video_path=_make_video(tmp_path, "other.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Other",
        enabled=False,
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(shown, off))
    comp, plan = _build_plan(stage)
    assert [s.enabled for s in comp.stages[0].secondaries] == [True, False]
    assert [a.cam.label for a in plan.cam_alignments] == ["Inset"]


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


# --- segment cache -----------------------------------------------------------


def _writes_output(calls: list[list[str]]):
    """A runner that records each argv and writes its last token, as
    ffmpeg writes its output file."""

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        argv = list(args[0])
        calls.append(argv)
        Path(argv[-1]).write_bytes(b"encoded " + argv[-1].encode())
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    return run


def _is_concat(argv: list[str]) -> bool:
    return "-f" in argv and argv[argv.index("-f") + 1] == "concat"


def test_a_repeat_render_encodes_nothing_and_stitches_the_cached_segments(tmp_path: Path) -> None:
    """The third identical YouTube export of Hostfinalen XI re-encoded
    every stage; with the cache the repeat is a stitch only."""
    from splitsmith.segment_cache import SegmentCache

    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="m")
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    first: list[list[str]] = []
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "one.mp4",
        work_dir=tmp_path / "w1",
        runner=_writes_output(first),
        segment_cache=cache,
    )
    assert [_is_concat(a) for a in first] == [False, False, True]

    second: list[list[str]] = []
    steps: list[mp4_render.RenderStep] = []
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "two.mp4",
        work_dir=tmp_path / "w2",
        runner=_writes_output(second),
        segment_cache=cache,
        progress=steps.append,
    )
    assert len(second) == 1 and _is_concat(second[0])
    listed = (tmp_path / "w2" / "concat.txt").read_text()
    assert listed.count(str((tmp_path / "cache").resolve())) == 2
    assert [(s.label, s.status, s.index, s.total) for s in steps] == [
        ("A", "reused", 1, 3),
        ("B", "reused", 2, 3),
        ("the match video", "stitching", 3, 3),
    ]


def test_a_changed_stage_is_the_only_segment_encoded_again(tmp_path: Path) -> None:
    from splitsmith.segment_cache import SegmentCache

    stage_a = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    stage_b = _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4")
    comp = composition.from_stage_compositions([stage_a, stage_b], project_name="m")
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "one.mp4",
        work_dir=tmp_path / "w1",
        runner=_writes_output([]),
        segment_cache=cache,
    )
    (tmp_path / "b.mp4").write_bytes(b"re-cut")  # a re-trim moves size and mtime

    again: list[list[str]] = []
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "two.mp4",
        work_dir=tmp_path / "w2",
        runner=_writes_output(again),
        segment_cache=cache,
    )
    encodes = [a for a in again if not _is_concat(a)]
    assert len(encodes) == 1
    assert str(tmp_path / "b.mp4") in encodes[0]


def test_a_failed_encode_leaves_no_cache_entry(tmp_path: Path) -> None:
    from splitsmith.segment_cache import SegmentCache

    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp = composition.from_stage_compositions([stage], project_name="m")
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)

    def half_then_fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        Path(args[0][-1]).write_bytes(b"half")
        raise subprocess.CalledProcessError(returncode=1, cmd=args[0], stderr="killed")

    with pytest.raises(mp4_render.FFmpegError):
        mp4_render.render_mp4(
            comp,
            output_path=tmp_path / "o.mp4",
            work_dir=tmp_path / "w",
            runner=half_then_fail,
            segment_cache=cache,
        )
    assert list((tmp_path / "cache").iterdir()) == []


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

    def __init__(self, *, motion_seconds: float = 0.0) -> None:
        self.calls: list[str] = []
        self.motion_seconds = motion_seconds
        self.frame_requests: list[tuple] = []
        self.frames_rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        import io

        from PIL import Image

        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        """A card drawn through its Look template: recorded as the JSON of
        what it was handed, so the text assertions below read the same
        list whichever path drew it."""
        import json

        self.calls.append(json.dumps(context.data, ensure_ascii=False))
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(
        self, template, *, context, width: int, height: int, fps: float, max_seconds: float
    ):
        """A still unless ``motion_seconds`` is set; frames are blank and
        counted in ``frames_rendered`` as they are pulled."""
        import json
        import math

        from splitsmith.overlay_raster import TemplateFrames

        self.calls.append(json.dumps(context.data, ensure_ascii=False))
        self.frame_requests.append((template, context.model_dump(), width, height, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))
        blank = bytes(width * height * 4)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield blank

        return TemplateFrames(
            duration=duration, frame_count=count, width=width, height=height, frames=frames()
        )


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


def _transitioned_composition(
    tmp_path: Path,
    *,
    kind: str | None = "fade",
    seconds: float = 1.0,
    slates: bool = False,
    summaries: bool = False,
    head_pad: float = 3.0,
    tail_pad: float = 10.0,
    head_pad_b: float | None = None,
    lower_thirds: bool = False,
) -> composition.Composition:
    """Two 20 s stages with a beep at 5 s and the last shot at 6.3 s, so
    with the default pads each stage keeps 2 s of handle before the head
    pad and 4 s after the tail pad (effective 14.3 s); one transition
    between them."""
    from splitsmith.match_project import StageScorecard
    from splitsmith.stage_summary_data import TileShot, TileStageData

    stage_a = _basic_stage(
        tmp_path=tmp_path, name="A", primary_name="a.mp4", head_pad=head_pad, tail_pad=tail_pad
    )
    stage_b = _basic_stage(
        tmp_path=tmp_path,
        name="B",
        primary_name="b.mp4",
        head_pad=head_pad if head_pad_b is None else head_pad_b,
        tail_pad=tail_pad,
    )
    titles = None
    if slates or lower_thirds:
        style = "slate" if slates else "lower-third"
        titles = {
            0: composition.TitleCard(text="Stage 1", duration_seconds=1.5, style=style),
            1: composition.TitleCard(text="Stage 2", duration_seconds=1.5, style=style),
        }
    holds = None
    if summaries:
        data = TileStageData(
            label="Me",
            stage_number=1,
            shots=(TileShot(1.0, 1.0), TileShot(1.3, 0.3)),
            stage_time_seconds=4.5,
            scorecard=StageScorecard(hit_factor=12.0, alphas=10),
        )
        hold = composition.SummaryHold(data=data, label="Me", duration_seconds=3.0)
        holds = {0: hold, 1: hold}
    return composition.from_stage_compositions(
        [stage_a, stage_b],
        project_name="m",
        titles=titles,
        summaries=holds,
        transitions=(
            (
                composition.Transition(
                    from_stage_index=0, to_stage_index=1, kind=kind, duration_seconds=seconds  # type: ignore[arg-type]
                ),
            )
            if kind is not None
            else ()
        ),
    )


def test_plan_timeline_places_a_boundary_between_a_summary_and_the_next_slate(tmp_path: Path) -> None:
    """Issue #1244: a transition sits between the last item of a stage's
    run and the first of the next, which are the summary hold and the
    slate when the composition has them; each side gives up d/2."""
    plan = mp4_render.plan_timeline(_transitioned_composition(tmp_path, slates=True, summaries=True))
    assert [item.kind for item in plan.items] == ["slate", "stage", "summary", "slate", "stage", "summary"]
    (boundary,) = plan.boundaries
    assert (boundary.after_index, boundary.kind, boundary.duration_seconds) == (2, "fade", 1.0)
    assert plan.items[2].tail_cut_seconds == 0.5 and plan.items[3].head_cut_seconds == 0.5
    assert plan.items[2].duration_seconds == pytest.approx(2.5)
    assert plan.items[3].duration_seconds == pytest.approx(1.0)
    assert plan.degradations == ()
    # A centred crossfade: the boundary is d long and each neighbour gave
    # up d/2, so the timeline is exactly the cut's length.
    assert plan.duration_seconds == pytest.approx(1.5 + 14.3 + 3.0 + 1.5 + 14.3 + 3.0)


def test_plan_timeline_places_a_boundary_between_two_bare_stages(tmp_path: Path) -> None:
    plan = mp4_render.plan_timeline(_transitioned_composition(tmp_path, kind="dissolve"))
    (boundary,) = plan.boundaries
    assert (boundary.after_index, boundary.kind) == (0, "dissolve")
    assert plan.items[0].tail_cut_seconds == 0.5 and plan.items[1].head_cut_seconds == 0.5
    assert [item.duration_seconds for item in plan.items] == [pytest.approx(13.8), pytest.approx(13.8)]
    assert plan.duration_seconds == pytest.approx(28.6)


def test_plan_timeline_without_transitions_has_no_boundaries_and_no_cuts(tmp_path: Path) -> None:
    plan = mp4_render.plan_timeline(_carded_composition(tmp_path))
    assert plan.boundaries == () and plan.degradations == ()
    assert all(item.head_cut_seconds == item.tail_cut_seconds == 0.0 for item in plan.items)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"seconds": 7.0},
            "transition before stage 'B' (7s) exceeds the stage's head pad (3s); "
            "increase the pad or shorten the transition: rendered as a cut",
        ),
        (
            {"seconds": 1.0, "head_pad_b": 5.0},
            None,  # no handle before the pad: the boundary pads it with a held frame
        ),
        (
            {"seconds": 1.0, "slates": True, "summaries": True, "head_pad_b": 5.0},
            None,
        ),
    ],
)
def test_a_transition_that_does_not_fit_is_a_cut_and_a_degradation(
    tmp_path: Path, kwargs: dict[str, object], message: str | None
) -> None:
    """Review Focus 1: the pads keep the beep and the last shot out of the
    fade and the trim must hold the handle; a miss is reported and the
    boundary becomes a cut, never a shorter fade. A card neighbour (the
    third case: summary into slate) has no pad to check."""
    plan = mp4_render.plan_timeline(_transitioned_composition(tmp_path, **kwargs))  # type: ignore[arg-type]
    if message is None:
        assert len(plan.boundaries) == 1 and plan.degradations == ()
        return
    assert plan.boundaries == ()
    assert plan.degradations == (message,)
    assert all(item.head_cut_seconds == item.tail_cut_seconds == 0.0 for item in plan.items)


def test_a_transition_longer_than_a_card_is_a_cut(tmp_path: Path) -> None:
    plan = mp4_render.plan_timeline(_transitioned_composition(tmp_path, seconds=4.0, slates=True))
    assert plan.boundaries == ()
    assert plan.degradations == (
        "transition into slate_001 (4s) exceeds half the card (0.75s): rendered as a cut",
    )


def test_narrow_plan_recomputes_the_cams_from_the_new_head_trim(tmp_path: Path) -> None:
    cam = SecondaryClip(
        video_path=_make_video(tmp_path, "cam.mp4"), video=_meta_30fps(), beep_offset_seconds=4.0, label="Cam"
    )
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(cam,), head_pad=3.0)
    _, plan = _build_plan(stage)
    narrowed = mp4_render._narrow_plan(plan, head_cut=0.5, tail_cut=0.25)
    assert narrowed.head_trim_seconds == pytest.approx(plan.head_trim_seconds + 0.5)
    assert narrowed.effective_seconds == pytest.approx(plan.effective_seconds - 0.75)
    assert narrowed.tail_trim_seconds == pytest.approx(plan.tail_trim_seconds + 0.25)
    reference = _basic_stage(
        tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(cam,), head_pad=2.5
    )
    _, expected = _build_plan(reference)
    got, want = narrowed.cam_alignments[0], expected.cam_alignments[0]
    assert (got.cam_seek_seconds, got.cam_spine_start) == (want.cam_seek_seconds, want.cam_spine_start)
    # A negative head cut reads handle footage before the pad.
    widened = mp4_render._narrow_plan(plan, head_cut=-0.5, tail_cut=0.0)
    assert widened.head_trim_seconds == pytest.approx(plan.head_trim_seconds - 0.5)
    assert widened.effective_seconds == pytest.approx(plan.effective_seconds + 0.5)


def test_edge_plans_cover_half_the_cut_and_half_the_handle(tmp_path: Path) -> None:
    """Issue #1244: a stage's tail edge is its last d/2 of effective footage
    plus d/2 of the trim past the tail pad; the head edge is d/2 before the
    head pad plus the first d/2. Both are d long."""
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", head_pad=3.0, tail_pad=10.0)
    _, plan = _build_plan(stage)
    tail = mp4_render._edge_plan(plan, half=0.5, end="tail")
    head = mp4_render._edge_plan(plan, half=0.5, end="head")
    assert tail.effective_seconds == pytest.approx(1.0) and head.effective_seconds == pytest.approx(1.0)
    assert tail.head_trim_seconds == pytest.approx(plan.head_trim_seconds + plan.effective_seconds - 0.5)
    assert head.head_trim_seconds == pytest.approx(plan.head_trim_seconds - 0.5)


def test_build_boundary_command_crossfades_two_equal_edges(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    cmd = mp4_render._build_boundary_command(
        tmp_path / "edge_003_tail.mp4",
        tmp_path / "edge_004_head.mp4",
        kind="zoom",
        seconds=1.0,
        sequence=comp.sequence,
        output_path=tmp_path / "boundary_003.mp4",
    )
    assert cmd[:3] == ("ffmpeg", "-hide_banner", "-y")
    assert cmd[3:7] == ("-i", str(tmp_path / "edge_003_tail.mp4"), "-i", str(tmp_path / "edge_004_head.mp4"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph == (
        "[0:v][1:v]xfade=transition=zoomin:duration=1:offset=0,format=yuv420p[final];"
        "[0:a][1:a]acrossfade=d=1:c1=tri:c2=tri[aout]"
    )
    assert cmd[cmd.index("-map") :][:4] == ("-map", "[final]", "-map", "[aout]")
    assert cmd[cmd.index("-t") + 1] == "1"
    assert cmd[-1] == str(tmp_path / "boundary_003.mp4")
    assert "-c:v" in cmd and cmd[cmd.index("-crf") + 1] == "20"


def test_build_motion_card_command_can_offset_or_delay_the_clip(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    base = mp4_render._build_motion_card_command(
        tmp_path / "bd.png",
        tmp_path / "clip.mov",
        seconds=1.0,
        sequence=comp.sequence,
        output_path=tmp_path / "o",
    )
    same = mp4_render._build_motion_card_command(
        tmp_path / "bd.png",
        tmp_path / "clip.mov",
        seconds=1.0,
        sequence=comp.sequence,
        output_path=tmp_path / "o",
        clip_offset_seconds=0.0,
        clip_delay_seconds=0.0,
    )
    assert same == base, "zero offsets change nothing"
    offset = mp4_render._build_motion_card_command(
        tmp_path / "bd.png",
        tmp_path / "clip.mov",
        seconds=1.0,
        sequence=comp.sequence,
        output_path=tmp_path / "o",
        clip_offset_seconds=2.5,
    )
    assert "-ss" not in offset, "an input seek past the clip's end yields nothing; the filter clones first"
    offset_graph = offset[offset.index("-filter_complex") + 1]
    assert (
        "setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=3.5,trim=start=2.5:end=3.5,setpts=PTS-STARTPTS[motion]"
        in offset_graph
    )
    delayed = mp4_render._build_motion_card_command(
        tmp_path / "bd.png",
        tmp_path / "clip.mov",
        seconds=1.0,
        sequence=comp.sequence,
        output_path=tmp_path / "o",
        clip_delay_seconds=0.5,
    )
    graph = delayed[delayed.index("-filter_complex") + 1]
    assert (
        "setpts=PTS-STARTPTS,tpad=start_duration=0.5:start_mode=add:color=black@0.0,tpad=stop_mode=clone"
        in graph
    )


def test_build_stage_command_threads_the_lower_third_window(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp, plan = _build_plan(stage)
    card = composition.TitleCard(text="Stage 1", duration_seconds=4.0, style="lower-third")
    png = tmp_path / "lt.png"
    plain = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "s.mp4",
        lower_third=mp4_render._LowerThirdInput(path=png, card=card),
    )
    delayed = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "s.mp4",
        lower_third=mp4_render._LowerThirdInput(path=png, card=card, delay_seconds=0.5),
    )
    at = plain.index(str(png))
    assert plain[at - 3 : at - 1] == ("-t", "4")
    at = delayed.index(str(png))
    assert delayed[at - 3 : at - 1] == ("-t", "4.5"), "the looped PNG lasts until the window closes"
    assert "enable='between(t,0.5,4.5)'" in delayed[delayed.index("-filter_complex") + 1]
    skipped = mp4_render._build_stage_command(
        plan,
        sequence=comp.sequence,
        output_path=tmp_path / "s.mp4",
        lower_third=mp4_render._LowerThirdInput(path=png, card=card, skip_seconds=0.5),
    )
    at = skipped.index(str(png))
    assert skipped[at - 3 : at - 1] == ("-t", "3.5")
    assert "enable='between(t,0,3.5)'" in skipped[skipped.index("-filter_complex") + 1]


def _render(tmp_path: Path, comp: composition.Composition, *, name: str, runner: Any, **kwargs: Any) -> Any:
    return mp4_render.render_mp4(
        comp,
        output_path=tmp_path / f"{name}.mp4",
        work_dir=tmp_path / f"work_{name}",
        runner=runner,
        **kwargs,
    )


def _names(calls: list[list[str]]) -> list[str]:
    return [Path(argv[-1]).name for argv in calls]


def _concat_names(work: Path) -> list[str]:
    return [Path(line.split("'")[1]).name for line in (work / "concat.txt").read_text().splitlines()]


def test_transitions_keep_the_timeline_length(tmp_path: Path) -> None:
    """Review Focus 2: a centred fade consumes d/2 of each neighbour and
    the boundary is d long, so the stitched length is the cut's length and
    the chapters need no change."""
    cut = _render(
        tmp_path, _transitioned_composition(tmp_path, kind=None), name="cut", runner=_writes_output([])
    )
    fade = _render(tmp_path, _transitioned_composition(tmp_path), name="fade", runner=_writes_output([]))
    assert fade.duration_seconds == pytest.approx(cut.duration_seconds) == pytest.approx(28.6)


def test_render_encodes_edges_then_the_boundary_then_trimmed_neighbours(tmp_path: Path) -> None:
    """Issue #1244: the boundary is decided (both edges and the xfade
    encoded) before the item that opens it is encoded trimmed, so a
    failure can still fall back to a cut."""
    calls: list[list[str]] = []
    _render(
        tmp_path, _transitioned_composition(tmp_path, kind="dissolve"), name="m", runner=_writes_output(calls)
    )
    assert _names(calls) == [
        "edge_000_tail.mp4",
        "edge_001_head.mp4",
        "boundary_000.mp4",
        "stage_000.mp4",
        "stage_001.mp4",
        "m.mp4",
    ]
    tail, head, boundary, stage_0, stage_1, concat = calls
    # A's effective window is source 2.0 .. 16.3; the tail edge reads 15.8 .. 16.8.
    assert (tail[tail.index("-ss") + 1], tail[tail.index("-t") + 1]) == ("15.8", "1")
    # B's starts at 2.0; the head edge reads 1.5 .. 2.5.
    assert (head[head.index("-ss") + 1], head[head.index("-t") + 1]) == ("1.5", "1")
    assert "xfade=transition=dissolve:duration=1:offset=0" in boundary[boundary.index("-filter_complex") + 1]
    assert boundary[boundary.index("-i") + 1].endswith("edge_000_tail.mp4")
    assert (stage_0[stage_0.index("-ss") + 1], stage_0[stage_0.index("-t") + 1]) == ("2", "13.8")
    assert (stage_1[stage_1.index("-ss") + 1], stage_1[stage_1.index("-t") + 1]) == ("2.5", "13.8")
    assert _concat_names(tmp_path / "work_m") == ["stage_000.mp4", "boundary_000.mp4", "stage_001.mp4"]
    assert "-c:a" in concat, "a boundary is a generated segment: the stitch re-encodes audio"


def test_a_failed_edge_leaves_the_neighbours_untrimmed(tmp_path: Path) -> None:
    """Review Focus 4: an edge that ffmpeg cannot encode costs the
    transition, never a gap; both neighbours keep their full length."""
    calls: list[list[str]] = []
    writing = _writes_output(calls)

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        if str(args[0][-1]).endswith("edge_000_tail.mp4"):
            raise subprocess.CalledProcessError(1, list(args[0]), stderr="boom")
        return writing(*args, **kwargs)

    result = _render(tmp_path, _transitioned_composition(tmp_path), name="m", runner=runner)
    assert result.degradations == (
        "transition after stage_000 failed to render (ffmpeg failed (exit 1): boom); rendered as a cut",
    )
    assert _names(calls) == ["stage_000.mp4", "stage_001.mp4", "m.mp4"]
    stage_0, stage_1, concat = calls
    assert (stage_0[stage_0.index("-ss") + 1], stage_0[stage_0.index("-t") + 1]) == ("2", "14.3")
    assert (stage_1[stage_1.index("-ss") + 1], stage_1[stage_1.index("-t") + 1]) == ("2", "14.3")
    assert _concat_names(tmp_path / "work_m") == ["stage_000.mp4", "stage_001.mp4"]
    assert "-c:a" not in concat and result.duration_seconds == pytest.approx(28.6)


def test_a_fit_failure_reaches_the_render_result(tmp_path: Path) -> None:
    result = _render(
        tmp_path, _transitioned_composition(tmp_path, seconds=7.0), name="m", runner=_writes_output([])
    )
    assert result.degradations == (
        "transition before stage 'B' (7s) exceeds the stage's head pad (3s); "
        "increase the pad or shorten the transition: rendered as a cut",
    )


def test_a_transition_render_is_reused_from_the_cache_until_a_neighbour_changes(tmp_path: Path) -> None:
    """The boundary keys on its two edge files and each edge on its
    source: a repeat render stitches only; a changed source re-encodes
    its edge, the boundary and its own segment, not the other side."""
    import os

    from splitsmith.segment_cache import SegmentCache

    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    comp = _transitioned_composition(tmp_path)
    first: list[list[str]] = []
    _render(tmp_path, comp, name="one", runner=_writes_output(first), segment_cache=cache)
    assert len(first) == 6
    second: list[list[str]] = []
    _render(tmp_path, comp, name="two", runner=_writes_output(second), segment_cache=cache)
    assert _names(second) == ["two.mp4"]
    source = comp.stages[0].primary.path
    source.write_bytes(source.read_bytes() + b"recut")
    os.utime(source, (source.stat().st_atime + 10, source.stat().st_mtime + 10))
    third: list[list[str]] = []
    _render(tmp_path, comp, name="three", runner=_writes_output(third), segment_cache=cache)
    # The cache writes partial files, so look at the commands: A's tail
    # edge, the boundary and A's own segment, then the stitch; B's edge
    # and segment stay cached.
    third_cmds = [" ".join(argv) for argv in third]
    assert len(third) == 4
    assert sum("-ss 15.8 -t 1 " in cmd for cmd in third_cmds) == 1
    assert sum("xfade=" in cmd for cmd in third_cmds) == 1
    assert sum("-ss 2 -t 13.8 " in cmd for cmd in third_cmds) == 1
    assert not any("-ss 2.5 -t 13.8 " in cmd or "-ss 1.5 -t 1 " in cmd for cmd in third_cmds)
    assert _is_concat(third[-1])


def test_a_transition_reports_three_more_progress_steps(tmp_path: Path) -> None:
    steps: list[mp4_render.RenderStep] = []
    _render(
        tmp_path,
        _transitioned_composition(tmp_path),
        name="m",
        runner=_writes_output([]),
        progress=steps.append,
    )
    assert [s.total for s in steps] == [6] * 6
    assert [s.index for s in steps] == [1, 2, 3, 4, 5, 6]
    assert [s.status for s in steps] == ["encoding"] * 5 + ["stitching"]
    assert steps[2].label == "transition 1"


def test_a_lower_third_shorter_than_the_cut_is_left_to_the_boundary(tmp_path: Path) -> None:
    """Review of #1244: a 0.5 s card with a 1 s fade left the trimmed stage
    a looped PNG with ``-t 0`` (ffmpeg: no limit, an endless encode) and a
    shorter one a negative ``-t``. A card the head edge has shown in full
    is dropped from the trimmed stage."""
    card = composition.TitleCard(text="S", duration_seconds=0.5, style="lower-third")
    lt = mp4_render._LowerThirdInput(path=tmp_path / "lt.png", card=card)
    assert mp4_render._trimmed_lower_third(lt, head_cut=0.5) is None
    assert mp4_render._trimmed_lower_third(lt, head_cut=0.6) is None
    kept = mp4_render._trimmed_lower_third(lt, head_cut=0.25)
    assert kept is not None and kept.skip_seconds == 0.25 and kept.shown_seconds == pytest.approx(0.25)
    assert mp4_render._trimmed_lower_third(lt, head_cut=0.0) is lt
    assert mp4_render._trimmed_lower_third(None, head_cut=0.5) is None


def test_edge_plans_shrink_to_the_handle_the_trim_holds(tmp_path: Path) -> None:
    """Review of #1244: at the default 5 s pads over a 5 s buffer there is
    no footage past the pad, and requiring some made every stage-to-stage
    transition a cut. The edge takes what the trim has; the boundary pads
    the rest with a held frame."""
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", head_pad=5.0, tail_pad=10.0)
    _, plan = _build_plan(stage)
    assert plan.head_trim_seconds == 0.0
    assert mp4_render._edge_handle(plan, half=0.5, end="head") == 0.0
    assert mp4_render._edge_handle(plan, half=0.5, end="tail") == 0.5
    head = mp4_render._edge_plan(plan, half=0.5, end="head")
    assert (head.head_trim_seconds, head.effective_seconds) == (0.0, pytest.approx(0.5))
    tail = mp4_render._edge_plan(plan, half=0.5, end="tail")
    assert tail.effective_seconds == pytest.approx(1.0)


def test_build_boundary_command_pads_a_short_edge_with_a_held_frame(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    common = {"kind": "fade", "seconds": 1.0, "sequence": comp.sequence, "output_path": tmp_path / "b.mp4"}
    head_short = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", head_pad_seconds=0.5, **common
    )
    assert head_short[head_short.index("-filter_complex") + 1] == (
        "[1:v]tpad=start_mode=clone:start_duration=0.5[hv];[1:a]adelay=500:all=1[ha];"
        "[0:v][hv]xfade=transition=fade:duration=1:offset=0,format=yuv420p[final];"
        "[0:a][ha]acrossfade=d=1:c1=tri:c2=tri[aout]"
    )
    tail_short = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", tail_pad_seconds=0.25, **common
    )
    assert tail_short[tail_short.index("-filter_complex") + 1] == (
        "[0:v]tpad=stop_mode=clone:stop_duration=0.25[tv];[0:a]apad=pad_dur=0.25[ta];"
        "[tv][1:v]xfade=transition=fade:duration=1:offset=0,format=yuv420p[final];"
        "[ta][1:a]acrossfade=d=1:c1=tri:c2=tri[aout]"
    )
    plain = mp4_render._build_boundary_command(tmp_path / "t.mp4", tmp_path / "h.mp4", **common)
    assert plain[plain.index("-filter_complex") + 1].startswith("[0:v][1:v]xfade=")


def test_render_pads_a_missing_handle_instead_of_cutting(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    result = _render(
        tmp_path, _transitioned_composition(tmp_path, head_pad_b=5.0), name="m", runner=_writes_output(calls)
    )
    assert result.degradations == ()
    assert _names(calls)[:3] == ["edge_000_tail.mp4", "edge_001_head.mp4", "boundary_000.mp4"]
    head = calls[1]
    assert (head[head.index("-ss") + 1], head[head.index("-t") + 1]) == ("0", "0.5")
    boundary = calls[2]
    assert (
        "[1:v]tpad=start_mode=clone:start_duration=0.5[hv]" in boundary[boundary.index("-filter_complex") + 1]
    )
    # B keeps its whole 5 s head pad (effective 16.3 s); the fade adds nothing.
    assert result.duration_seconds == pytest.approx(14.3 + 16.3)


def test_a_head_edges_lower_third_opens_at_the_stage_start_not_half_a_fade_late(tmp_path: Path) -> None:
    """Review of the grid slice, same line here: the lower third opens
    ``handle`` into the head edge (the boundary prepends the rest), so a
    stage with no footage before its pad shows the card from the edge's
    first frame rather than half a fade late."""
    calls: list[list[str]] = []
    _render(
        tmp_path,
        _transitioned_composition(tmp_path, head_pad_b=5.0, lower_thirds=True),
        name="m",
        runner=_writes_output(calls),
        rasterizer=_FakeRasterizer(),
    )
    head_edge = next(c for c in calls if c[-1].endswith("edge_001_head.mp4"))
    graph = head_edge[head_edge.index("-filter_complex") + 1]
    assert "enable='lt(t,1.5)'" in graph
    trimmed = next(c for c in calls if c[-1].endswith("stage_001.mp4"))
    assert (
        "enable='between(t,0,1)'" in trimmed[trimmed.index("-filter_complex") + 1]
    ), "continues from 0.5 s in"


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
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=24",
            "-filter_complex",
            "[1:a]volume='between(t,10,10.2)':eval=frame[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "25",
            "-keyint_min",
            "25",
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
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
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=8",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
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


# --- motion cards (slice 2, #1242) ----------------------------------------------


def _fake_clip_writer(frames, *, out: Path, fps: float, ffmpeg_binary: str):
    """Consumes the frames and touches the file, so the renderer tests
    never spawn ffmpeg for the clip."""
    from splitsmith.look_motion import MotionClip

    count = sum(1 for _ in frames.frames)
    out.write_bytes(b"clip")
    return MotionClip(path=out, seconds=count / fps, frame_count=count)


def _command_writing(runner: MagicMock, suffix: str) -> tuple[str, ...]:
    return next(tuple(c.args[0]) for c in runner.call_args_list if str(c.args[0][-1]).endswith(suffix))


def test_build_motion_card_command_overlays_the_clip_on_the_backdrop_and_holds(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    cmd = mp4_render._build_motion_card_command(
        tmp_path / "bd.png",
        tmp_path / "clip.mov",
        seconds=3.0,
        sequence=comp.sequence,
        output_path=tmp_path / "t.mp4",
    )
    i_flags = [i for i, t in enumerate(cmd) if t == "-i"]
    assert cmd[i_flags[0] + 1] == str(tmp_path / "bd.png")
    assert cmd[i_flags[0] - 4 : i_flags[0]] == (
        "-loop",
        "1",
        "-framerate",
        mp4_render._rate_string(comp.sequence),
    )
    assert cmd[i_flags[1] + 1] == str(tmp_path / "clip.mov")
    assert cmd[i_flags[2] + 1].startswith("anullsrc")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=3" in graph
    assert "[0:v][motion]overlay=0:0:format=auto[withmotion]" in graph
    assert graph.endswith("[withmotion]format=yuv420p,setsar=1[final]")
    assert cmd[cmd.index("-t") + 1] == "3"
    assert cmd[cmd.index("-map") + 1] == "[final]" and "2:a" in cmd


def test_render_mp4_encodes_an_animated_card_as_a_motion_segment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer(motion_seconds=0.6)
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    title_cmd = _command_writing(runner, "title_page.mp4")
    assert str(work / "title_page_motion.mov") in title_cmd
    assert str(work / "title_page_backdrop.png") in title_cmd
    assert "-loop" in title_cmd, "the backdrop still loops; the clip is the second input"
    assert (work / "title_page_backdrop.png").exists()
    assert fake.frames_rendered == 4 * 18, "four cards, 0.6 s at 30 fps each"


def test_render_mp4_keeps_the_still_path_for_a_still_template(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    title_cmd = _command_writing(runner, "title_page.mp4")
    assert str(work / "title_page.png") in title_cmd
    assert not any("motion.mov" in t for t in title_cmd)
    assert fake.frames_rendered == 4


def test_a_cached_motion_card_renders_no_frames(tmp_path: Path, monkeypatch) -> None:
    """Second render, same inputs: the segment comes from the cache and
    the template is loaded but no frame is rendered or piped."""
    from splitsmith.segment_cache import SegmentCache

    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    comp = _carded_composition(tmp_path)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    first = _FakeRasterizer(motion_seconds=0.6)
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m1.mp4",
        work_dir=tmp_path / "w1",
        runner=_writes_output([]),
        rasterizer=first,
        segment_cache=cache,
    )
    assert first.frames_rendered > 0
    second = _FakeRasterizer(motion_seconds=0.6)
    calls: list[list[str]] = []
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m2.mp4",
        work_dir=tmp_path / "w2",
        runner=_writes_output(calls),
        rasterizer=second,
        segment_cache=cache,
    )
    assert second.frames_rendered == 0
    assert len(second.frame_requests) == 4, "the templates were loaded to compute the keys"
    # The backdrop grabs still run (the backdrop PNG's content is part of
    # the key); nothing else but the stitch does.
    assert sum(_is_concat(c) for c in calls) == 1
    assert all(_is_concat(c) or str(c[-1]).endswith(".png") for c in calls), calls


def test_an_animated_lower_third_is_a_clip_input_with_the_clip_filters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    comp = _carded_composition(tmp_path, lower_third=True)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer(motion_seconds=0.4)
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    stage_cmd = _command_writing(runner, "stage_000.mp4")
    clip = str(work / "lower_third_000_motion.mov")
    assert clip in stage_cmd
    graph = stage_cmd[stage_cmd.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=1.5" in graph and "fade=t=out:st=1:d=0.5:alpha=1[lt]" in graph
    lt_index = stage_cmd.index(clip)
    assert (
        stage_cmd[lt_index - 1] == "-i" and stage_cmd[lt_index - 2] != "-t"
    ), "a clip input is not looped or cut"


def test_the_compositions_shooters_reach_every_card(tmp_path: Path) -> None:
    import dataclasses
    import json

    comp = _carded_composition(tmp_path)
    comp = dataclasses.replace(
        comp,
        shooters=(
            composition.CompositionShooter(label="Mathias", accent="#ff2d2d", logo_path=None, club="PK"),
        ),
    )
    runner = MagicMock(side_effect=_ok)
    fake = _FakeRasterizer()
    mp4_render.render_mp4(
        comp, output_path=tmp_path / "m.mp4", work_dir=tmp_path / "work", runner=runner, rasterizer=fake
    )
    for call in fake.calls:
        data = json.loads(call)
        assert data["shooters"] == [{"label": "Mathias", "accent": "#ff2d2d", "club": "PK", "logo": None}]


def test_the_summary_hold_carries_the_shooters_accent(tmp_path: Path) -> None:
    import dataclasses

    comp = _summarised_composition(tmp_path)
    comp = dataclasses.replace(
        comp,
        shooters=(composition.CompositionShooter(label="Me", accent="#abcdef", logo_path=None, club=None),),
    )
    fake = _FakeRasterizer()
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m.mp4",
        work_dir=tmp_path / "w",
        runner=MagicMock(side_effect=_ok),
        rasterizer=fake,
    )
    holds = [c for c in fake.calls if c.startswith("<!doctype html>")]
    assert holds and all('<div class="cell" style="--accent:#abcdef">' in h for h in holds)


# --- stings (issue #1245) ---------------------------------------------------------------


def _sting_render(
    tmp_path: Path,
    *,
    kind: str = "sting:wipe",
    rasterizer: Any = None,
    name: str = "m",
    head_pad_b: float | None = None,
    comp: composition.Composition | None = None,
    **kwargs: Any,
) -> tuple[Any, list[list[str]], Any]:
    """Two stages with one ``kind`` transition rendered through the fake
    runner and a fake rasterizer whose sting animates for 1 s."""
    calls: list[list[str]] = []
    fake = _FakeRasterizer(motion_seconds=1.0) if rasterizer is None else rasterizer
    if comp is None:
        comp = _transitioned_composition(tmp_path, kind=kind, head_pad_b=head_pad_b)
    result = _render(tmp_path, comp, name=name, runner=_writes_output(calls), rasterizer=fake, **kwargs)
    return result, calls, fake


def _boundary_argv(calls: list[list[str]]) -> list[str]:
    return next(argv for argv in calls if argv[-1].endswith("boundary_000.mp4"))


def _inputs(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv) if token == "-i"]


def test_build_boundary_command_lays_a_sting_clip_over_the_fade(tmp_path: Path) -> None:
    """Issue #1245: the clip is a third input laid over the crossfaded
    video for the whole boundary before the final pixel format; without
    a clip the argv is slice 4's, byte for byte."""
    comp = _carded_composition(tmp_path)
    args: dict[str, Any] = {
        "seconds": 1.0,
        "sequence": comp.sequence,
        "output_path": tmp_path / "boundary_003.mp4",
    }
    base = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", kind="sting:wipe", **args
    )
    plain = mp4_render._build_boundary_command(tmp_path / "t.mp4", tmp_path / "h.mp4", kind="fade", **args)
    assert base == plain, "a sting without its clip is the fade it rides"
    stung = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", kind="sting:wipe", sting_clip=tmp_path / "s.mov", **args
    )
    assert _inputs(stung) == [str(tmp_path / "t.mp4"), str(tmp_path / "h.mp4"), str(tmp_path / "s.mov")]
    rate = mp4_render._rate_string(comp.sequence)
    assert stung[stung.index("-filter_complex") + 1] == (
        "[0:v][1:v]xfade=transition=fade:duration=1:offset=0[xf];"
        f"[2:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,trim=0:1[motion];"
        "[xf][motion]overlay=0:0:format=auto[stung];"
        "[stung]format=yuv420p[final];"
        "[0:a][1:a]acrossfade=d=1:c1=tri:c2=tri[aout]"
    )
    assert stung[stung.index("-map") :][:4] == ("-map", "[final]", "-map", "[aout]")
    assert stung[stung.index("-t") + 1] == "1"


def test_a_sting_adds_the_clip_input_and_overlay_to_the_boundary_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    result, calls, fake = _sting_render(tmp_path)
    assert result.degradations == ()
    clip = tmp_path / "work_m" / "boundary_000_sting.mov"
    boundary = _boundary_argv(calls)
    assert _inputs(boundary)[2] == str(clip)
    graph = boundary[boundary.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=1:offset=0[xf]" in graph
    assert "[xf][motion]overlay=0:0:format=auto[stung];[stung]format=yuv420p[final]" in graph
    assert clip.exists(), "the clip is written before the boundary encodes"
    assert fake.frames_rendered == 30, "one second of sting at 30 fps, rendered once"
    others = [argv for argv in calls if argv is not boundary]
    assert others and not any(token.endswith("_sting.mov") for argv in others for token in argv)
    assert _names(calls) == [
        "edge_000_tail.mp4",
        "edge_001_head.mp4",
        "boundary_000.mp4",
        "stage_000.mp4",
        "stage_001.mp4",
        "m.mp4",
    ]


def test_a_sting_overlays_the_whole_boundary_when_an_edge_is_padded(tmp_path: Path, monkeypatch) -> None:
    """Review Focus 1: B's head pad equals its beep offset, so its trim
    holds no handle and the boundary clones the head edge's first frame
    for half the fade; the sting still runs from the boundary's t=0."""
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    result, calls, _ = _sting_render(tmp_path, head_pad_b=5.0)
    assert result.degradations == ()
    graph = _boundary_argv(calls)[_boundary_argv(calls).index("-filter_complex") + 1]
    assert "[1:v]tpad=start_mode=clone:start_duration=0.5[hv]" in graph
    assert ",trim=0:1[motion]" in graph and "start_duration=0.5:start_mode=add" not in graph


def test_a_sting_the_look_lacks_is_a_fade_with_a_degradation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    result, calls, fake = _sting_render(tmp_path, kind="sting:nope")
    assert result.degradations == (
        "sting nope is not in the splitsmith Look; transition after stage_000 rendered as a fade",
    )
    boundary = _boundary_argv(calls)
    assert len(_inputs(boundary)) == 2
    graph = boundary[boundary.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=1:offset=0,format=yuv420p[final]" in graph
    assert fake.frames_rendered == 0
    assert result.duration_seconds == pytest.approx(28.6)


def test_a_sting_without_a_browser_is_a_fade_with_a_degradation(tmp_path: Path, monkeypatch) -> None:
    """No rasterizer at all (the preflight found no Chromium): the
    boundary is still a fade, named as such, never a crash."""
    from splitsmith.overlay_raster import RasterizerUnavailableError

    class _NoChromium:
        def __enter__(self):  # type: ignore[no-untyped-def]
            raise RasterizerUnavailableError("no browser", "no Chromium on this host")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(mp4_render, "ChromiumRasterizer", _NoChromium)
    calls: list[list[str]] = []
    comp = _transitioned_composition(tmp_path, kind="sting:wipe")
    result = _render(tmp_path, comp, name="m", runner=_writes_output(calls))
    assert result.degradations == (
        "generated cards and summaries skipped: no Chromium on this host",
        "sting wipe: no browser to draw it; transition after stage_000 rendered as a fade",
    )
    assert len(_inputs(_boundary_argv(calls))) == 2


def test_a_sting_whose_frames_fail_becomes_a_cut(tmp_path: Path, monkeypatch) -> None:
    """Review Focus 2: a template that throws on its second frame leaves
    no clip and costs the transition, never the render."""
    import dataclasses

    from splitsmith.look_motion import MotionClipError

    class _Breaking(_FakeRasterizer):
        def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
            frames = super().render_template_frames(
                template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
            )
            blank = bytes(width * height * 4)

            def broken():  # type: ignore[no-untyped-def]
                yield blank
                raise RuntimeError("boom on frame 2")

            return dataclasses.replace(frames, frames=broken())

    def writer_that_wraps(frames, *, out: Path, fps: float, ffmpeg_binary: str):  # type: ignore[no-untyped-def]
        # ``write_motion_clip``'s contract: the template's own failure is the
        # clip's, as MotionClipError, and no file is left behind.
        try:
            for _ in frames.frames:
                pass
        except Exception as exc:  # noqa: BLE001
            out.unlink(missing_ok=True)
            raise MotionClipError(f"{out.name}: {exc}") from exc
        out.write_bytes(b"clip")

    monkeypatch.setattr(mp4_render, "write_motion_clip", writer_that_wraps)
    result, calls, _ = _sting_render(tmp_path, rasterizer=_Breaking(motion_seconds=1.0))
    assert result.degradations == (
        "transition after stage_000 failed to render (boundary_000_sting.mov: boom on frame 2); "
        "rendered as a cut",
    )
    assert _names(calls) == [
        "edge_000_tail.mp4",
        "edge_001_head.mp4",
        "stage_000.mp4",
        "stage_001.mp4",
        "m.mp4",
    ]
    assert not (tmp_path / "work_m" / "boundary_000_sting.mov").exists()
    assert result.duration_seconds == pytest.approx(28.6)


def test_a_cached_boundary_with_a_sting_renders_no_frame(tmp_path: Path, monkeypatch) -> None:
    """The clip is a virtual input keyed by the template digest: a repeat
    render pulls no frame, and a different kind is a different boundary."""
    import dataclasses

    from splitsmith.segment_cache import SegmentCache

    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    comp = _transitioned_composition(tmp_path, kind="sting:wipe")
    _, first, fake_one = _sting_render(tmp_path, name="one", comp=comp, segment_cache=cache)
    assert len(first) == 6 and fake_one.frames_rendered == 30
    _, second, fake_two = _sting_render(tmp_path, name="two", comp=comp, segment_cache=cache)
    assert _names(second) == ["two.mp4"] and fake_two.frames_rendered == 0
    faded = dataclasses.replace(
        comp, transitions=tuple(dataclasses.replace(t, kind="fade") for t in comp.transitions)
    )
    _, third, _ = _sting_render(tmp_path, name="three", comp=faded, segment_cache=cache)
    assert sum("xfade=" in " ".join(argv) for argv in third) == 1, "a fade is not the sting's boundary"
    assert len(third) == 2, "the edges and stages are the same segments: only the boundary and the stitch"


def test_a_card_whose_template_fails_is_named_in_the_degradations(tmp_path: Path) -> None:
    """A broken template used to drop its card with only a log line; the
    export reported success with the card missing (#1265 review)."""
    from splitsmith.overlay_raster import TemplateScriptError

    class _Broken(_FakeRasterizer):
        def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
            if context.data["card"]["slot"] == "title_page":
                raise TemplateScriptError(f"{Path(template).name}: line 4: boom")
            return super().render_template_frames(
                template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
            )

    comp = _carded_composition(tmp_path)
    result = mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m.mp4",
        work_dir=tmp_path / "work",
        runner=MagicMock(side_effect=_ok),
        rasterizer=_Broken(),
    )
    notes = [d for d in result.degradations if "line 4: boom" in d]
    assert len(notes) == 1 and "left out" in notes[0], result.degradations


# --- logo spots (spec 2026-10-09) -------------------------------------------------


def _green_logo(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "logo-green.png"
    Image.new("RGBA", (64, 64), (0, 255, 0, 255)).save(path)
    return path


def _sting_brand(fake: Any) -> list[Any]:
    """What each sting frame request's data said about the brand."""
    return [request[1]["data"].get("brand") for request in fake.frame_requests]


def test_the_wipe_spot_hands_the_sting_your_brand(tmp_path: Path, monkeypatch) -> None:
    import dataclasses

    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    logo = _green_logo(tmp_path)
    comp = dataclasses.replace(
        _transitioned_composition(tmp_path, kind="sting:wipe"),
        logo_spots=frozenset({"wipe"}),
        brand=composition.BrandMark(logo_path=logo, line="Bulletbard"),
    )
    _result, _calls, fake = _sting_render(tmp_path, comp=comp)
    assert _sting_brand(fake) == [{"logo": logo.resolve().as_uri(), "line": "Bulletbard"}]


def test_without_the_wipe_spot_the_sting_context_is_as_before(tmp_path: Path, monkeypatch) -> None:
    import dataclasses

    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    comp = dataclasses.replace(
        _transitioned_composition(tmp_path, kind="sting:wipe"),
        logo_spots=frozenset({"summaries"}),
        brand=composition.BrandMark(logo_path=_green_logo(tmp_path), line=None),
    )
    _result, _calls, fake = _sting_render(tmp_path, comp=comp)
    assert fake.frame_requests and all("brand" not in r[1]["data"] for r in fake.frame_requests)


def _hold_pixels(tmp_path: Path, spots: frozenset[str]) -> Any:
    import dataclasses

    from PIL import Image

    comp = dataclasses.replace(
        _summarised_composition(tmp_path),
        shooters=(
            composition.CompositionShooter(
                label="Me", accent=None, logo_path=_green_logo(tmp_path), club=None
            ),
        ),
        logo_spots=spots,
    )
    work = tmp_path / ("w-" + "-".join(sorted(spots)))
    mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m.mp4",
        work_dir=work,
        runner=MagicMock(side_effect=_ok),
        rasterizer=_FakeRasterizer(),
    )
    stills = sorted(p for p in work.glob("*.png") if "summary" in p.name)
    assert stills, sorted(p.name for p in work.iterdir())
    with Image.open(stills[0]) as image:
        return image.convert("RGB").copy()


def test_the_summaries_spot_puts_the_shooters_logo_top_right(tmp_path: Path) -> None:
    image = _hold_pixels(tmp_path, frozenset({"summaries"}))
    side = round(image.height * 0.09)
    margin = round(image.height * 0.04)
    assert image.getpixel((image.width - margin - side // 2, margin + side // 2)) == (0, 255, 0)


def test_without_the_summaries_spot_the_hold_has_no_logo(tmp_path: Path) -> None:
    image = _hold_pixels(tmp_path, frozenset())
    greens = sum(1 for r, g, b in image.getdata() if g > 200 and r < 60 and b < 60)
    assert greens == 0
