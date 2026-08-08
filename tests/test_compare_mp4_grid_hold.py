"""The post-stage hold: its duration model and its video half.

Milestone B freezes every tile at the end of a stage and draws that
shooter's summary over their own cell. This module covers the
*arithmetic* that makes room for it and the *argv* that fills it -- the
still's input, the ``concat`` that joins it to the action, and the clock
windows either side. Composing the still itself is
``test_compare_overlay_summary.py``; whether any of this reaches the
pixels is ``test_compare_grid_overlay_integration.py``, and that is the
only place it can be answered (see below).

The whole thing turns on two durations that must not be confused:

``duration_seconds``
    the **action**: head pad + the longest post-beep span + tail pad.
    Footage, tile chains and ``xstack`` run for exactly this long, and
    that is what freezing means -- the picture stops here.

``total_seconds``
    the **segment**: ``duration_seconds + hold_seconds``. Every audio
    chain runs this long, carrying silence through the hold.

The reason every chain must agree is not that ``concat -c copy`` would
refuse a segment whose streams disagree in *length*. Measured on ffmpeg
6.1.1, it does not: it exits 0 with no warning in either direction. What
it refuses is a disagreement in stream *layout* -- count, codec,
parameters -- and that is a separate, pre-existing invariant. A length
mismatch is the quiet failure instead: audio short of its video
collapses at the AAC re-encode and runs every later stage early,
accumulating (-3000ms after one 3s-short segment, -9000ms after three),
while audio *longer* than its video freezes the last coded frame and
stays in sync (+0.1ms across four segments). See
``GridStagePlan.total_seconds`` for the full measurement.

Extending the tile chains to ``total_seconds`` instead would run the
footage on underneath the summary rather than freezing it, and would
look almost right in a thumbnail. Hence
:func:`test_tile_chains_still_run_only_the_action`.

**What no test in this module can tell you.** Every assertion here is
over an argv string. The failure the hold is most exposed to -- a
segment built with no still in it -- produces a *valid* argv, a render
that exits 0, a stitch that exits 0, a container declaring the right
length and a freeze at the right instant, on the raw last action frame
with no summary drawn on it. ``build_stage_command`` refusing that shape
(see :func:`test_a_hold_with_no_still_is_refused_rather_than_built`) is
the structural guard. The *evidence* lives in the integration module,
where a decoded duration catches a still that is missing and a frame
sampled from inside the hold catches one that is merely wrong.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.overlay_raster import RasterizerUnavailableError
from tests.conftest import fake_ffmpeg_probe

ACTION = 12.5
HOLD = 3.0
TOTAL = 15.5


# --- fixtures --------------------------------------------------------------


def _tile(label: str, row: int, col: int, present: bool, lead: float) -> mp4_grid.GridTile:
    return mp4_grid.GridTile(
        label=label,
        trim_path=Path(f"/trims/{label}.mov") if present else None,
        beep_offset_in_clip=1.25,
        seek_seconds=0.25 if present else 0.0,
        lead_pad_seconds=lead,
        source_duration_seconds=6.0 if present else 0.0,
        row=row,
        col=col,
    )


def _plan(
    labels: tuple[str, ...] = ("Ann", "Bo", "Cy"),
    *,
    fillers: int = 1,
    rows: int = 2,
    cols: int = 2,
    hold: float | None = None,
) -> mp4_grid.GridStagePlan:
    """A plan mirroring the default-off matrix in the commands tests.

    ``hold=None`` omits the argument entirely, which is how every caller
    that predates Milestone B constructs one. Three shooters in a 2x2
    leaves one unreached cell, one tile is filler (two inputs, not one)
    and the last tile is lead-padded, so a chain that silently uses the
    wrong duration has somewhere to show it.
    """
    tiles = []
    for index, label in enumerate(labels):
        row, col = divmod(index, cols)
        tiles.append(_tile(label, row, col, index >= fillers, 0.5 if index == len(labels) - 1 else 0.0))
    kwargs = {} if hold is None else {"hold_seconds": hold}
    return mp4_grid.GridStagePlan(
        stage_number=3,
        stage_name="Stage 3",
        tiles=tuple(tiles),
        duration_seconds=ACTION,
        audio_label=labels[0],
        rows=rows,
        cols=cols,
        **kwargs,
    )


def _stage(n: int, *, trim: Path, beep: float, duration: float) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=trim,
        audit_path=Path("/nonexistent.json"),
        beep_offset_in_clip=beep,
        duration_seconds=duration,
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _bundle(label: str, stages: dict[int, CompareStageBundle]) -> CompareShooterBundle:
    return CompareShooterBundle(label=label, project_root=Path(f"/p/{label}"), stages_by_number=stages)


#: Where the frozen summary still would be, for a plan carrying a hold.
#:
#: ``build_stage_command`` is pure, so this never has to exist on disk.
HOLD_STILL = Path("/w/summary-stage3.png")


def _command(
    plan: mp4_grid.GridStagePlan,
    *,
    overlay: mp4_grid.StageOverlayPlan | None = None,
    hold_still_path: Path | None = HOLD_STILL,
) -> tuple[str, ...]:
    """One stage command, with the still supplied whenever the plan holds.

    Supplied by default rather than per-test because a hold with no still
    is not a configuration this module is testing -- it is the one
    ``build_stage_command`` refuses outright, since it is the segment
    whose audio outlasts its video and whose wrongness nothing downstream
    reports. ``hold_still_path=None`` opts back out to pin that refusal.
    A zero-hold plan ignores it either way, which is what keeps the
    no-flags argv assertions below meaningful.
    """
    return mp4_grid.build_stage_command(
        plan,
        canvas=mp4_grid.GridCanvas(1920, 1080, 25, 1),
        output_path=Path("/w/s3.mov"),
        ffmpeg_binary="/bin/ffmpeg",
        overlay=overlay,
        hold_still_path=hold_still_path,
    )


def _overlay_plan(
    tmp_path: Path, *, clocks: tuple[mp4_grid.TileClock, ...] = ()
) -> mp4_grid.StageOverlayPlan:
    list_path = tmp_path / "sprites.txt"
    list_path.write_text("file '/tmp/a.png'\nduration 12.5\nfile '/tmp/a.png'\n")
    return mp4_grid.StageOverlayPlan(
        sprite_list_path=list_path,
        font_path=tmp_path / "font.ttf",
        font_size=64,
        clocks=clocks,
    )


def _graph_of(cmd: tuple[str, ...]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def _chains(graph: str, pattern: str) -> list[str]:
    """Every ``;``-separated filter chain whose output label matches."""
    return [part for part in graph.split(";") if re.search(pattern, part)]


def _input_durations(cmd: tuple[str, ...]) -> list[str]:
    """Every ``-t`` value, i.e. how much of each input ffmpeg reads."""
    return [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-t"]


# --- the model -------------------------------------------------------------


def test_hold_defaults_to_zero_and_total_equals_duration():
    # Every construction site that predates Milestone B omits the field.
    plan = _plan(hold=None)
    assert plan.hold_seconds == 0.0
    assert plan.total_seconds == plan.duration_seconds == ACTION


@pytest.mark.parametrize(
    "action,hold,total",
    [(12.5, 3.0, 15.5), (12.5, 0.0, 12.5), (0.0, 4.0, 4.0), (9.75, 0.25, 10.0)],
)
def test_total_seconds_is_action_plus_hold(action: float, hold: float, total: float):
    plan = mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=(_tile("Ann", 0, 0, True, 0.0),),
        duration_seconds=action,
        audio_label="Ann",
        rows=1,
        cols=1,
        hold_seconds=hold,
    )
    assert plan.total_seconds == pytest.approx(total)
    # The action is never rewritten by the hold: that is the freeze.
    assert plan.duration_seconds == pytest.approx(action)


def test_hold_seconds_reaches_every_plan():
    # Three stages, two shooters, one of whom skips a stage -- the hold is
    # a whole-render setting, so no stage may come out with a different one.
    anders = _bundle(
        "Anders",
        {
            1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0),
            2: _stage(2, trim=Path("/a2.mp4"), beep=1.0, duration=9.0),
        },
    )
    mathias = _bundle(
        "Mathias",
        {
            1: _stage(1, trim=Path("/m1.mp4"), beep=3.0, duration=14.0),
            2: _stage(2, trim=Path("/m2.mp4"), beep=1.0, duration=9.0),
            3: _stage(3, trim=Path("/m3.mp4"), beep=1.0, duration=20.0),
        },
    )

    plans = mp4_grid.build_stage_plans(
        [mathias, anders],
        audio_label="Mathias",
        head_pad_seconds=1.0,
        tail_pad_seconds=0.5,
        hold_seconds=HOLD,
    )

    assert [p.stage_number for p in plans] == [1, 2, 3]
    assert [p.hold_seconds for p in plans] == [HOLD, HOLD, HOLD]
    for plan in plans:
        assert plan.total_seconds == pytest.approx(plan.duration_seconds + HOLD)


def test_hold_defaults_to_zero_through_build_stage_plans():
    anders = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    (plan,) = mp4_grid.build_stage_plans(
        [anders], audio_label="Anders", head_pad_seconds=1.0, tail_pad_seconds=0.5
    )
    assert plan.hold_seconds == 0.0
    assert plan.total_seconds == plan.duration_seconds


def test_negative_hold_is_rejected():
    # A negative hold makes the segment shorter than its own action, so the
    # audio ends before the video. Measured: the stitch does not refuse
    # that, it accepts it silently and the shortfall becomes accumulating
    # A/V drift, so nothing downstream will ever catch it. This guard is
    # the only thing that does.
    #
    # Matched on the planner's own spelling of the message, not just on
    # "negative": ``GridStagePlan.__post_init__`` rejects it too, so a
    # looser match passes with the planner's guard deleted and the caller
    # told which *field* is wrong rather than which argument it passed.
    anders = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    with pytest.raises(ValueError, match=r"negative: got hold_seconds=-0\.5"):
        mp4_grid.build_stage_plans(
            [anders],
            audio_label="Anders",
            head_pad_seconds=1.0,
            tail_pad_seconds=0.5,
            hold_seconds=-0.5,
        )


def test_negative_hold_is_rejected_on_a_hand_built_plan():
    # ``build_stage_command`` takes plans direct from callers that never
    # went through ``build_stage_plans`` (the UI's endpoint, the render
    # tests, and any future planner), so the guard cannot live only in the
    # planner. This is the dataclass's own message, not the planner's.
    with pytest.raises(ValueError, match=r"negative: got -0\.5\."):
        _plan(hold=-0.5)


# --- the default-off guarantee ---------------------------------------------


#: One stage command, captured verbatim from ``main`` at ``b6732de``.
#:
#: Not a hash: a hash proves something moved, this says what. Three
#: shooters, one filler, one lead-padded tile, one unreached cell.
MAIN_STAGE_ARGV: tuple[str, ...] = (
    "/bin/ffmpeg",
    "-hide_banner",
    "-y",
    "-f",
    "lavfi",
    "-t",
    "12.5",
    "-i",
    "color=c=black:s=960x540:r=25/1",
    "-f",
    "lavfi",
    "-t",
    "12.5",
    "-i",
    "anullsrc=channel_layout=stereo:sample_rate=48000",
    "-ss",
    "0.25",
    "-t",
    "12.5",
    "-i",
    "/trims/Bo.mov",
    "-ss",
    "0.25",
    "-t",
    "12",
    "-i",
    "/trims/Cy.mov",
    "-f",
    "lavfi",
    "-t",
    "12.5",
    "-i",
    "color=c=black:s=960x540:r=25/1",
    "-filter_complex",
    (
        "[0:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25/1,"
        "tpad=stop_duration=12.5:stop_mode=add:color=black,trim=0:12.5[t0];"
        "[2:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25/1,"
        "tpad=stop_duration=12.5:stop_mode=add:color=black,trim=0:12.5[t1];"
        "[3:v]tpad=start_duration=0.5:start_mode=add:color=black,setpts=PTS-STARTPTS,"
        "scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25/1,"
        "tpad=stop_duration=12.5:stop_mode=add:color=black,trim=0:12.5[t2];"
        "[4:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,"
        "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25/1,"
        "tpad=stop_duration=12.5:stop_mode=add:color=black,trim=0:12.5[e0];"
        "[t0][t1][t2][e0]xstack=inputs=4:layout=0_0|960_0|0_540|960_540[grid];"
        "[grid]format=yuv420p[final];"
        "[1:a]asetpts=PTS-STARTPTS,aresample=async=1,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "apad,atrim=0:12.5,asplit=2[a0][m0];"
        "[2:a]asetpts=PTS-STARTPTS,aresample=async=1,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "apad,atrim=0:12.5,asplit=2[a1][m1];"
        "[3:a]asetpts=PTS-STARTPTS,adelay=500:all=1,aresample=async=1,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "apad,atrim=0:12.5,asplit=2[a2][m2];"
        "[m0][m1][m2]amix=inputs=3:normalize=1[amix]"
    ),
    "-map",
    "[final]",
    "-map",
    "[amix]",
    "-map",
    "[a0]",
    "-map",
    "[a1]",
    "-map",
    "[a2]",
    "-disposition:a:0",
    "default",
    "-disposition:a:1",
    "0",
    "-disposition:a:2",
    "0",
    "-disposition:a:3",
    "0",
    "-metadata:s:a:0",
    "title=Mix",
    "-metadata:s:a:0",
    "handler_name=Mix",
    "-metadata:s:a:1",
    "title=Ann",
    "-metadata:s:a:1",
    "handler_name=Ann",
    "-metadata:s:a:2",
    "title=Bo",
    "-metadata:s:a:2",
    "handler_name=Bo",
    "-metadata:s:a:3",
    "title=Cy",
    "-metadata:s:a:3",
    "handler_name=Cy",
    "-r",
    "25/1",
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "20",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "pcm_s16le",
    "/w/s3.mov",
)

#: sha256 over :func:`_zero_hold_matrix`, measured on ``main`` at ``b6732de``.
#:
#: The single argv above is one shape; this is 18 of them across three
#: rosters (3 in a 2x2 leaves an unreached cell, 6 in a 2x3 is a shape
#: where a rows/cols swap is not a no-op), three filler counts and two
#: canvases. Regenerate only alongside a *deliberate* change to the
#: no-flags path, and say so in the commit.
ZERO_HOLD_ARGV_SHA256 = "1d8e6d717f6f63d48b3d4804cd77839f94f1cbdb46b3dec7b9a8b4a3fcad7ba2"

_ZERO_HOLD_ROSTERS = (
    (("Ann", "Bo", "Cy"), 2, 2),
    (("Ann", "Bo", "Cy", "Di", "Ed"), 3, 3),
    (("Ann", "Bo", "Cy", "Di", "Ed", "Fi"), 2, 3),
)


def _zero_hold_matrix() -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    for labels, rows, cols in _ZERO_HOLD_ROSTERS:
        for fillers in (0, 1, 2):
            plan = _plan(labels, fillers=fillers, rows=rows, cols=cols, hold=0.0)
            for canvas in (
                mp4_grid.GridCanvas(3840, 2160, 30000, 1001),
                mp4_grid.GridCanvas(1920, 1080, 25, 1),
            ):
                out.append(
                    mp4_grid.build_stage_command(
                        plan,
                        canvas=canvas,
                        output_path=Path("/w/s3.mov"),
                        ffmpeg_binary="/bin/ffmpeg",
                    )
                )
    return out


def test_zero_hold_produces_the_command_main_produces_today():
    assert _command(_plan(hold=0.0)) == MAIN_STAGE_ARGV
    # ...and so does a plan that never heard of the field.
    assert _command(_plan(hold=None)) == MAIN_STAGE_ARGV

    matrix = _zero_hold_matrix()
    assert len(matrix) == 18
    fingerprint = hashlib.sha256(json.dumps(matrix, indent=0).encode()).hexdigest()
    assert fingerprint == ZERO_HOLD_ARGV_SHA256, (
        "an explicit hold_seconds=0.0 moved the no-flags argv. The stitch "
        "stream-copies video and refuses segments that disagree, hours in."
    )


def test_zero_hold_leaves_the_concat_stitch_alone():
    # The hold lives inside the stage segment precisely so the stitch stays
    # a dumb copy. Nothing about it may become hold-aware.
    assert mp4_grid.build_concat_command(
        list_path=Path("/w/c.txt"),
        output_path=Path("/o/g.mp4"),
        ffmpeg_binary="/bin/ffmpeg",
        audio_labels=("Ann", "Bo", "Cy"),
    ) == (
        "/bin/ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        "/w/c.txt",
        "-map",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-metadata:s:a:0",
        "title=Mix",
        "-metadata:s:a:0",
        "handler_name=Mix",
        "-metadata:s:a:1",
        "title=Ann",
        "-metadata:s:a:1",
        "handler_name=Ann",
        "-metadata:s:a:2",
        "title=Bo",
        "-metadata:s:a:2",
        "handler_name=Bo",
        "-metadata:s:a:3",
        "title=Cy",
        "-metadata:s:a:3",
        "handler_name=Cy",
        "-disposition:a:0",
        "default",
        "-disposition:a:1",
        "0",
        "-disposition:a:2",
        "0",
        "-disposition:a:3",
        "0",
        "-movflags",
        "+faststart",
        "/o/g.mp4",
    )


# --- what the hold does and does not extend --------------------------------


def test_audio_chains_run_the_whole_segment_including_the_hold():
    graph = _graph_of(_command(_plan(hold=HOLD)))
    chains = _chains(graph, r"\[a\d+\]\[m\d+\]$")

    assert len(chains) == 3  # one per tile, filler included
    for chain in chains:
        assert f"atrim=0:{TOTAL:g}" in chain, chain
        # ``apad`` pads without a bound and ``atrim`` sets the length, so
        # the action's duration must not survive anywhere in the chain.
        assert f"atrim=0:{ACTION:g}" not in chain, chain


def test_the_silent_filler_track_also_runs_the_whole_segment():
    # A shooter with no trim for the stage contributes ``anullsrc``, whose
    # input ``-t`` is the action. If ``apad`` did not carry it through the
    # hold, that one track would end early and take the segment's uniform
    # stream layout with it.
    graph = _graph_of(_command(_plan(fillers=1, hold=HOLD)))
    (filler_chain,) = _chains(graph, r"\[a0\]\[m0\]$")
    assert "apad," in filler_chain
    assert f"atrim=0:{TOTAL:g}" in filler_chain


def test_tile_chains_still_run_only_the_action():
    """The freeze is the point: the footage stops at ``duration_seconds``.

    Running the tile chains to ``total_seconds`` would play the footage on
    underneath the summary instead of freezing it -- which looks almost
    right in a thumbnail and wrong in motion.
    """
    cmd = _command(_plan(hold=HOLD))
    graph = _graph_of(cmd)

    video_chains = _chains(graph, r"\[[te]\d+\]$")
    assert len(video_chains) == 4  # 3 tiles + 1 unreached cell
    for chain in video_chains:
        assert f"trim=0:{ACTION:g}[" in chain, chain
        assert f"tpad=stop_duration={ACTION:g}:" in chain, chain
        assert f"{TOTAL:g}" not in chain, chain

    # And no input reads past the action either -- the footage itself is
    # what freezes.
    assert f"{TOTAL:g}" not in _input_durations(cmd)


def test_the_sprite_chain_still_ends_at_the_action(tmp_path: Path):
    """The live overlay stops at the freeze and hands off to the summary.

    A shot counter and a last split still stepping over a blurred, dimmed
    summary is the "reads as a stall rather than a conclusion" failure the
    freeze exists to prevent. The sprite chain's own ``trim`` is what
    stops it, so the hold must not extend it.
    """
    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path)))
    (sprite_chain,) = _chains(graph, r"\[ovl\]$")

    assert f"tpad=stop_duration={ACTION:g}:stop_mode=clone" in sprite_chain, sprite_chain
    assert f"trim=0:{ACTION:g}[ovl]" in sprite_chain, sprite_chain
    assert f"{TOTAL:g}" not in sprite_chain, sprite_chain


def test_the_overlay_does_not_change_what_the_hold_extends(tmp_path: Path):
    # The overlay touches the video half only. With it on, the tile chains
    # must still stop at the action and the audio must still run the whole
    # segment -- the hold and the overlay are independent.
    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path)))

    for chain in _chains(graph, r"\[[te]\d+\]$"):
        assert f"trim=0:{ACTION:g}[" in chain, chain
        assert f"{TOTAL:g}" not in chain, chain
    for chain in _chains(graph, r"\[a\d+\]\[m\d+\]$"):
        assert f"atrim=0:{TOTAL:g}" in chain, chain
    # And the extra sprite input did not disturb the stream layout.
    cmd = _command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path))
    maps = [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-map"]
    assert maps == ["[final]", "[amix]", "[a0]", "[a1]", "[a2]"]


def test_every_audio_track_is_the_same_length_as_every_other():
    # Invariant 1, the half the hold can break. Extending some tracks and
    # not others is *not* rejected by the stitch -- measured, it exits 0 --
    # so the short track just runs early from the next stage on, further
    # early with each stage after. This assertion is the only alarm.
    for hold in (0.0, 0.25, HOLD):
        graph = _graph_of(_command(_plan(hold=hold)))
        lengths = set(re.findall(r"atrim=0:([\d.]+)", graph))
        assert lengths == {f"{ACTION + hold:g}"}, (hold, lengths)


def test_stream_counts_are_unchanged_by_the_hold():
    # N+1 audio (mix first, then shooters alphabetically) plus exactly one
    # video, whatever the hold is. Never add or drop a stream for it.
    for hold in (None, 0.0, HOLD):
        cmd = _command(_plan(hold=hold))
        maps = [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-map"]
        assert maps == ["[final]", "[amix]", "[a0]", "[a1]", "[a2]"], hold
        handlers = [
            value.removeprefix("handler_name=")
            for flag, value in zip(cmd, cmd[1:], strict=False)
            if flag.startswith("-metadata:s:a:") and value.startswith("handler_name=")
        ]
        assert handlers == ["Mix", "Ann", "Bo", "Cy"], hold


def test_the_hold_does_not_move_the_beep():
    """Invariant 2, restated exactly for Task 9's still input.

    Every tile's beep lands on the head pad, and the hold is appended
    after the action, so nothing about the front of a tile -- its seek,
    its lead pad, its ``tpad``/``setpts`` order -- may change.

    Narrowed rather than loosened. Task 7 could say "the two commands are
    identical but for the audio lengths"; Task 9 legitimately adds one
    input and two filter chains, so the equalities below name **exactly**
    what a hold is allowed to add and still compare everything else
    whole. A weaker spelling (`in`, or a prefix comparison) would stop
    noticing a tile input that moved.
    """
    plain = _command(_plan(hold=None))
    held = _command(_plan(hold=HOLD))

    # Every input the tiles read is untouched, and the still went after
    # all of them -- so no tile's stream index moved.
    assert held[: held.index("-loop")] == plain[: plain.index("-filter_complex")]
    assert held[held.index("-loop") : held.index("-filter_complex")] == (
        "-loop",
        "1",
        "-framerate",
        "25/1",
        "-t",
        f"{HOLD:g}",
        "-i",
        str(HOLD_STILL),
    )
    # The still reads for the hold and nothing else reads any longer than
    # it did: the footage still stops at the freeze.
    assert _input_durations(held) == [*_input_durations(plain), f"{HOLD:g}"]

    lead = "tpad=start_duration=0.5:start_mode=add:color=black,setpts=PTS-STARTPTS,"
    assert lead in _graph_of(held)
    assert "adelay=500:all=1,aresample=async=1," in _graph_of(held)

    # Two tiles + one filler + one unreached cell occupy inputs 0-4, so
    # the still is input 5.
    hold_chain = f"[5:v]setpts=PTS-STARTPTS,scale=1920:1080,setsar=1,fps=25/1,trim=0:{HOLD:g}[hold]"
    expected = _graph_of(plain).replace(f"atrim=0:{ACTION:g}", f"atrim=0:{TOTAL:g}")
    expected = expected.replace(
        "[grid]format=yuv420p[final]",
        f"{hold_chain};[grid][hold]concat=n=2:v=1:a=0[joined];[joined]format=yuv420p[final]",
    )
    assert expected == _graph_of(held)


# --- the hold's own video half ---------------------------------------------


def test_a_hold_with_no_still_is_refused_rather_than_built():
    """The segment shape almost nothing downstream reports.

    Measured on ffmpeg 6.1.1: a segment whose audio outlasts its video
    stitches at exit 0 with no warning, declares the right length in its
    container, freezes at exactly the right moment and stays A/V-locked
    within +0.1ms -- the picture simply holds the raw last action frame,
    unblurred, with no summary on it. A green render, a correct declared
    duration and an in-sync A/V measurement all pass against it. What
    does not is a duration counted from decoded frames, and the pixels of
    a held frame; both live in the integration module and both cost a
    real encode. This refusal costs nothing and runs first.
    """
    with pytest.raises(ValueError, match=r"hold_seconds=3 but no hold_still_path"):
        _command(_plan(hold=HOLD), hold_still_path=None)


def test_hold_still_input_is_appended_after_the_sprite_input(tmp_path: Path):
    # A filler tile takes two inputs where a real tile takes one and an
    # unreached cell adds another, so an input inserted anywhere but last
    # renumbers the streams behind it -- which lands one shooter's audio
    # in another shooter's track, silently.
    cmd = _command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path))
    inputs = [value for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-i"]

    assert inputs[-1] == str(HOLD_STILL)
    assert inputs[-2] == str(tmp_path / "sprites.txt")
    # And the graph reads it at the index that placement implies.
    assert f"[{len(inputs) - 1}:v]" in _graph_of(cmd)
    assert f"[{len(inputs) - 2}:v]format=rgba" in _graph_of(cmd)


def test_the_still_input_is_looped_for_exactly_the_hold_duration():
    cmd = _command(_plan(hold=HOLD))
    still = cmd[cmd.index("-loop") : cmd.index("-filter_complex")]

    assert still == ("-loop", "1", "-framerate", "25/1", "-t", f"{HOLD:g}", "-i", str(HOLD_STILL))
    # Not the segment and not the action: the still covers the hold alone.
    assert f"{TOTAL:g}" not in still
    assert f"{ACTION:g}" not in still


def test_hold_is_concatenated_after_the_action_not_composited_over_it():
    """``concat``, never ``overlay``.

    Compositing the still over the tail would leave the footage running
    underneath it and every ``drawtext`` clock ticking through it. The
    join is what makes the action genuinely stop.
    """
    graph = _graph_of(_command(_plan(hold=HOLD)))

    (still_chain,) = _chains(graph, r"\[hold\]$")
    assert still_chain.endswith(f"trim=0:{HOLD:g}[hold]"), still_chain
    # ``concat`` compares size, SAR and frame rate across its inputs.
    assert f"scale={1920}:{1080}" in still_chain
    assert "setsar=1" in still_chain
    assert "fps=25/1" in still_chain

    assert "[grid][hold]concat=n=2:v=1:a=0[joined]" in graph
    assert "[joined]format=yuv420p[final]" in graph
    # The still is joined on, not laid over the picture, and it carries
    # no audio -- the audio chains already run the whole segment.
    assert "[hold]overlay" not in graph
    assert "concat=n=2:v=1:a=1" not in graph


def test_the_sprite_overlay_does_not_reach_the_hold(tmp_path: Path):
    """The sprite is composited onto the action, upstream of the join.

    So the last shot counter and last split cannot step over the summary
    -- there is no expression to get wrong, only the graph's shape.
    """
    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path)))
    chains = graph.split(";")

    sprite = next(part for part in chains if part.endswith("[ovl]"))
    composite = next(part for part in chains if part.endswith("[ovlgrid]"))
    join = next(part for part in chains if "concat=n=2" in part)

    assert f"trim=0:{ACTION:g}[ovl]" in sprite, sprite
    assert chains.index(sprite) < chains.index(composite) < chains.index(join)
    # The join's first input is the composited action, so the sprite is
    # inside the half that ends at the freeze.
    assert join.startswith("[ovlgrid][hold]") or join.startswith("[ovltext][hold]"), join


def test_the_hold_does_not_touch_the_clock_windows(tmp_path: Path):
    """A hold changes the graph's *shape*, never the clock expressions.

    The ``drawtext`` filters hang off ``[ovlgrid]``, which is the action;
    the summary is joined after them. So their ``t`` is the action's own
    timeline and cannot reach a hold frame, and the open-ended windows
    stay open-ended.

    An earlier revision added ``*lt(t,duration)`` to the two unbounded
    ones. It changed no pixel of a rendered hold -- the in-hold frame came
    out byte-identical either way -- while making the same ``--overlay``
    render emit different ``enable`` text depending on an unrelated
    field. This test is the guard against that coming back. What stops a
    clock reaching the summary is
    :func:`test_hold_is_concatenated_after_the_action_not_composited_over_it`,
    and the pixels are checked in
    ``test_the_summary_hold_reaches_the_rendered_pixels``
    (``test_compare_grid_overlay_integration.py``), which samples a frame
    inside the hold and compares the clock corner of a shooter who has a
    clock against one who never does.
    """
    clocks = (
        # Ticking to a known freeze: bounded above by the freeze itself.
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),
        # No known end: the open-ended spelling, with no bound at all.
        mp4_grid.TileClock(row=1, col=0, start_seconds=1.0, freeze_seconds=None, final_text=None),
    )

    def _drawtext(hold: float) -> list[str]:
        graph = _graph_of(_command(_plan(hold=hold), overlay=_overlay_plan(tmp_path, clocks=clocks)))
        return [part for part in graph.split(",") if "drawtext=" in part or "enable=" in part]

    assert _drawtext(HOLD) == _drawtext(0.0)

    graph = _graph_of(_command(_plan(hold=HOLD), overlay=_overlay_plan(tmp_path, clocks=clocks)))
    assert r"enable='gte(t\,1)*lt(t\,6)'" in graph  # the ticking half
    assert r"enable='gte(t\,6)'" in graph  # the static hold, open above
    assert r"enable='gte(t\,1)'" in graph  # the open-ended tick
    # The freeze is the only upper bound any clock window carries.
    assert graph.count("lt(t\\,") == 1
    assert f"lt(t\\,{ACTION:g})" not in graph


def test_zero_hold_emits_no_still_input_and_no_concat(tmp_path: Path):
    for overlay in (None, _overlay_plan(tmp_path)):
        for hold in (None, 0.0):
            cmd = _command(_plan(hold=hold), overlay=overlay)
            assert "-loop" not in cmd, (hold, overlay)
            assert str(HOLD_STILL) not in cmd, (hold, overlay)
            graph = _graph_of(cmd)
            assert "concat=n=2" not in graph, (hold, overlay)
            assert "[hold]" not in graph, (hold, overlay)
            assert "[joined]" not in graph, (hold, overlay)


def test_stream_layout_is_identical_with_and_without_the_hold(tmp_path: Path):
    """Invariant 1. The hold extends every stream together or not at all.

    ``concat -c copy`` refuses segments whose stream *layout* disagrees --
    count, codec, parameters -- and it refuses at the very last step,
    after the whole match has been encoded. So a held stage and an
    unheld one have to present the same 1 video + N+1 audio, the same
    codecs and the same track identities.
    """
    for overlay in (None, _overlay_plan(tmp_path)):
        plain = _command(_plan(hold=0.0), overlay=overlay)
        held = _command(_plan(hold=HOLD), overlay=overlay)

        def layout(cmd: tuple[str, ...]) -> list[str]:
            keep = ("-map", "-disposition:a:", "-c:v", "-c:a", "-pix_fmt", "-r")
            return [
                f"{flag}={value}"
                for flag, value in zip(cmd, cmd[1:], strict=False)
                if flag.startswith(keep) or flag.startswith("-metadata:s:a:")
            ]

        assert layout(plain) == layout(held), overlay
        assert layout(held) == [
            "-map=[final]",
            "-map=[amix]",
            "-map=[a0]",
            "-map=[a1]",
            "-map=[a2]",
            "-disposition:a:0=default",
            "-disposition:a:1=0",
            "-disposition:a:2=0",
            "-disposition:a:3=0",
            "-metadata:s:a:0=title=Mix",
            "-metadata:s:a:0=handler_name=Mix",
            "-metadata:s:a:1=title=Ann",
            "-metadata:s:a:1=handler_name=Ann",
            "-metadata:s:a:2=title=Bo",
            "-metadata:s:a:2=handler_name=Bo",
            "-metadata:s:a:3=title=Cy",
            "-metadata:s:a:3=handler_name=Cy",
            "-r=25/1",
            "-c:v=libx264",
            "-pix_fmt=yuv420p",
            "-c:a=pcm_s16le",
        ], overlay


# --- the render driver ------------------------------------------------------


def _driver_shooters(tmp_path: Path) -> list[CompareShooterBundle]:
    """Two shooters, one stage, one of them with an audit on disk.

    The audit is what gives the summary something to write; the other
    shooter exercises the tile that has none.
    """
    bundles = []
    for label, shots in (("Anders", [0.9, 1.4]), ("Mathias", None)):
        audit = tmp_path / f"{label}-audit.json"
        if shots is not None:
            audit.write_text(
                json.dumps(
                    {
                        "shots": [
                            {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": int(t * 1000)}
                            for i, t in enumerate(shots)
                        ]
                    }
                ),
                encoding="utf-8",
            )
        trim = tmp_path / f"{label}.mov"
        trim.write_bytes(b"")
        stage = CompareStageBundle(
            stage_number=1,
            stage_name="Stage 1",
            trim_path=trim,
            audit_path=audit,
            beep_offset_in_clip=2.0,
            duration_seconds=10.0,
            width=1920,
            height=1080,
            frame_rate_num=25,
            frame_rate_den=1,
        )
        bundles.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number={1: stage})
        )
    return bundles


def _still_runner(written: list[tuple[str, ...]]):
    """A fake freeze-frame extractor that writes a real (tiny) PNG.

    ``build_hold_still`` opens what this writes, so a runner that only
    returns 0 would leave every cell black and the "the summary reached
    the still" assertions would pass against nothing.
    """

    def runner(cmd, **_kwargs):
        written.append(tuple(str(c) for c in cmd))
        Image.new("RGB", (64, 36), (7, 9, 11)).save(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return runner


def test_a_hold_without_the_overlay_is_refused_by_the_engine(tmp_path: Path):
    # The summary is the overlay's own data in the overlay's own
    # typography; a hold on a clean grid would be a blurred still with
    # nothing written on it. Refused, not silently accepted.
    with pytest.raises(mp4_grid.GridRenderError, match=r"needs overlay=True \(--overlay on the CLI\)"):
        mp4_grid.render_grid_mp4(
            _driver_shooters(tmp_path),
            audio_label="Anders",
            output_path=tmp_path / "grid.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
            overlay=False,
            summary_hold_seconds=2.0,
            runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""),
            work_dir=tmp_path / "work",
            ffmpeg_binary="/bin/ffmpeg",
        )


def test_the_hold_reaches_the_stage_command_and_writes_a_still(tmp_path: Path):
    calls: list[tuple[str, ...]] = []
    stills: list[tuple[str, ...]] = []

    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
        overlay=True,
        summary_hold_seconds=2.0,
        runner=runner,
        probe_runner=fake_ffmpeg_probe(),
        still_runner=_still_runner(stills),
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
    )

    still_path = work / "summary-stage1.png"
    assert still_path.exists()
    with Image.open(still_path) as image:
        assert image.size == (640, 360)

    stage_cmd = calls[0]
    assert "-loop" in stage_cmd
    assert str(still_path) in stage_cmd
    graph = stage_cmd[stage_cmd.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=0[joined]" in graph
    assert "trim=0:2[hold]" in graph
    # Every audio track runs the whole segment: 1.0 head + 8.0 post-beep
    # + 0.5 tail = 9.5 action, + 2.0 hold = 11.5.
    assert graph.count("atrim=0:11.5") == 2


def test_freeze_extraction_does_not_go_through_the_progress_runner(tmp_path: Path):
    """Both shipped callers count ``runner`` calls to say "stage N of M".

    A freeze frame pulled through that hook would advance the counter
    three times per stage and misreport every one of them.
    """
    calls: list[tuple[str, ...]] = []
    stills: list[tuple[str, ...]] = []

    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
        overlay=True,
        summary_hold_seconds=2.0,
        runner=runner,
        probe_runner=fake_ffmpeg_probe(),
        still_runner=_still_runner(stills),
        work_dir=tmp_path / "work",
        ffmpeg_binary="/bin/ffmpeg",
    )

    # One stage + one stitch, and not one call more.
    assert len(calls) == 2
    # Two present tiles, so two freeze frames, all on the other hook.
    assert len(stills) == 2
    assert all("-update" in cmd for cmd in stills)


def test_no_hold_writes_no_still_and_changes_no_command(tmp_path: Path):
    calls: list[tuple[str, ...]] = []
    stills: list[tuple[str, ...]] = []

    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
        overlay=True,
        runner=runner,
        probe_runner=fake_ffmpeg_probe(),
        still_runner=_still_runner(stills),
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
    )

    assert stills == []
    assert list(work.glob("summary-*.png")) == []
    assert "-loop" not in calls[0]
    assert "concat=n=2" not in calls[0][calls[0].index("-filter_complex") + 1]


def test_a_stage_whose_summary_still_fails_is_skipped_not_fatal(tmp_path: Path):
    """One bad stage is reported and skipped; the rest still stitch.

    That is the rule the whole stage loop follows -- a full-match 4K
    re-encode is far too long to lose to one stage -- and composing the
    still was the one step in it that could take the run down. The
    per-tile cases already degrade to a black cell inside
    ``overlay_summary``; what is left is whole-stage (a font that will
    not load, a disk that will not take the PNG).
    """
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def exploding_still(*_args, **_kwargs):
        raise OSError("no space left on device")

    shooters = _driver_shooters(tmp_path)
    # Two stages, so there is something left to stitch after the bad one.
    for bundle in shooters:
        bundle.stages_by_number[2] = dataclasses.replace(bundle.stages_by_number[1], stage_number=2)

    _real_still = mp4_grid._stage_hold_still

    def maybe_explode(plan, *args, **kwargs):
        if plan.stage_number == 1:
            exploding_still()
        return _real_still(plan, *args, **kwargs)

    mp4_grid._stage_hold_still = maybe_explode  # type: ignore[assignment]
    try:
        result = mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / "grid.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
            overlay=True,
            summary_hold_seconds=2.0,
            runner=runner,
            probe_runner=fake_ffmpeg_probe(),
            still_runner=_still_runner([]),
            work_dir=tmp_path / "work",
            ffmpeg_binary="/bin/ffmpeg",
        )
    finally:
        mp4_grid._stage_hold_still = _real_still  # type: ignore[assignment]

    assert [(o.stage_number, o.ok) for o in result.stages] == [(1, False), (2, True)]
    assert "no space left on device" in (result.failed[0].error or "")
    # Stage 1 never reached ffmpeg; stage 2 rendered and the stitch ran.
    assert len(calls) == 2
    assert calls[-1][calls[-1].index("-f") + 1] == "concat"


class _AlwaysUnavailableRasterizer:
    """Stands in for :class:`~splitsmith.overlay_raster.ChromiumRasterizer`
    at the ``render_grid_mp4`` call site: ``__enter__`` always raises
    :class:`~splitsmith.overlay_raster.RasterizerUnavailableError`, so the
    degradation test below never needs a real browser -- it is monkeypatched
    over the name ``mp4_grid.render_grid_mp4`` resolves when the caller
    leaves ``rasterizer`` at its default (``None``) and a hold needs one.
    """

    def __enter__(self) -> _AlwaysUnavailableRasterizer:
        raise RasterizerUnavailableError(
            "stage summary omitted: no usable Chromium",
            "fake: simulated missing browser for this test",
        )

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False  # pragma: no cover -- __enter__ always raises first


def test_no_usable_chromium_degrades_the_hold_instead_of_crashing(tmp_path: Path):
    """No usable browser must not crash a render.

    Verified by hand during the original task; this pins it. Two stages
    (mirroring the test above) so "the notice fires once, not once per
    stage" is an actual claim rather than trivially true of a one-stage
    fixture -- a notice per stage on a 12-stage match would be miserable
    to read. Both stages must still succeed: a missing browser costs the
    summary's text, never the render.
    """
    calls: list[tuple[str, ...]] = []
    stills: list[tuple[str, ...]] = []
    notices: list[str] = []

    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    shooters = _driver_shooters(tmp_path)
    for bundle in shooters:
        bundle.stages_by_number[2] = dataclasses.replace(bundle.stages_by_number[1], stage_number=2)

    real_chromium_rasterizer = mp4_grid.ChromiumRasterizer
    mp4_grid.ChromiumRasterizer = _AlwaysUnavailableRasterizer  # type: ignore[assignment,misc]
    try:
        result = mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / "grid.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, 25, 1),
            overlay=True,
            summary_hold_seconds=2.0,
            runner=runner,
            probe_runner=fake_ffmpeg_probe(),
            still_runner=_still_runner(stills),
            on_notice=notices.append,
            work_dir=tmp_path / "work",
            ffmpeg_binary="/bin/ffmpeg",
        )
    finally:
        mp4_grid.ChromiumRasterizer = real_chromium_rasterizer  # type: ignore[assignment,misc]

    # No crash, and neither stage is penalised for the missing browser.
    assert [(o.stage_number, o.ok) for o in result.stages] == [(1, True), (2, True)]
    assert len(result.degradations) == 1
    assert result.degradations[0].summary == "stage summary omitted: no usable Chromium"
    assert result.degradations[0].detail == "fake: simulated missing browser for this test"
    # Exactly once for the whole render, not once per stage.
    assert notices == ["fake: simulated missing browser for this test"]
    # Both holds still composed -- freeze/blur/dim, just with no summary
    # text -- rather than the stage being skipped outright.
    for stage_number in (1, 2):
        still_path = tmp_path / "work" / f"summary-stage{stage_number}.png"
        assert still_path.exists()
        with Image.open(still_path) as image:
            assert image.size == (640, 360)


# --- where a tile's own footage ends ----------------------------------------


def test_footage_end_is_the_head_pad_plus_this_tile_s_post_beep_span():
    """The two spellings of a tile's front collapse to the same answer.

    A tile either seeks into its clip (``seek_seconds > 0``, no lead pad)
    or cannot seek far enough back and gets a synthesised lead pad
    instead. Both put the beep on the head pad, so both must end at
    ``head_pad + (source - beep)`` -- and that is where the tile chain's
    black ``tpad`` starts.
    """
    # head_pad 1.0, beep 1.25 into a 6.0s clip: seeks 0.25, ends at 1.0+4.75.
    seeking = mp4_grid.GridTile(
        label="Bo",
        trim_path=Path("/trims/Bo.mov"),
        beep_offset_in_clip=1.25,
        seek_seconds=0.25,
        lead_pad_seconds=0.0,
        source_duration_seconds=6.0,
        row=0,
        col=1,
    )
    assert mp4_grid.tile_footage_end_seconds(seeking) == pytest.approx(5.75)

    # Same head pad, but the beep is only 0.4s in: the seek clamps at 0
    # and 0.6s of lead pad makes up the shortfall. Ends at 1.0 + 5.6.
    padded = mp4_grid.GridTile(
        label="Cy",
        trim_path=Path("/trims/Cy.mov"),
        beep_offset_in_clip=0.4,
        seek_seconds=0.0,
        lead_pad_seconds=0.6,
        source_duration_seconds=6.0,
        row=1,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(padded) == pytest.approx(6.6)


def test_footage_end_of_a_filler_tile_is_zero():
    """A filler has no source, so there is no footage to end.

    Callers must not paint a summary over it -- there is no shooter --
    and a filler that reported a positive end would arm one.
    """
    filler = mp4_grid.GridTile(
        label="Ann",
        trim_path=None,
        beep_offset_in_clip=0.0,
        seek_seconds=0.0,
        lead_pad_seconds=0.0,
        source_duration_seconds=0.0,
        row=0,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(filler) == 0.0


def test_footage_end_never_goes_negative():
    """A probe shorter than the seek is nonsense, not a negative time.

    ``source_duration_seconds`` comes off an ffprobe of the trim, so it
    can disagree with the seek by a rounding error rather than by a real
    quantity. Clamped here so no caller has to.
    """
    odd = mp4_grid.GridTile(
        label="Di",
        trim_path=Path("/trims/Di.mov"),
        beep_offset_in_clip=2.0,
        seek_seconds=1.0,
        lead_pad_seconds=0.0,
        source_duration_seconds=0.5,
        row=0,
        col=0,
    )
    assert mp4_grid.tile_footage_end_seconds(odd) == 0.0
