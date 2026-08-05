"""The post-stage hold's duration model.

Milestone B freezes every tile at the end of a stage and draws that
shooter's summary over their own cell. This module covers only the
*arithmetic* that makes room for it -- the composition (task 8) and the
filter graph's video half (task 9) are separate.

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
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle

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


def _command(
    plan: mp4_grid.GridStagePlan, *, overlay: mp4_grid.StageOverlayPlan | None = None
) -> tuple[str, ...]:
    return mp4_grid.build_stage_command(
        plan,
        canvas=mp4_grid.GridCanvas(1920, 1080, 25, 1),
        output_path=Path("/w/s3.mov"),
        ffmpeg_binary="/bin/ffmpeg",
        overlay=overlay,
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
    # Invariant 2. Every tile's beep lands on the head pad, and the hold is
    # appended after the action, so nothing about the front of a tile --
    # its seek, its lead pad, its ``tpad``/``setpts`` order -- may change.
    plain = _command(_plan(hold=None))
    held = _command(_plan(hold=HOLD))

    assert _input_durations(plain) == _input_durations(held)
    lead = "tpad=start_duration=0.5:start_mode=add:color=black,setpts=PTS-STARTPTS,"
    assert lead in _graph_of(held)
    assert "adelay=500:all=1,aresample=async=1," in _graph_of(held)
    # Only the audio lengths differ between the two graphs.
    assert _graph_of(plain).replace(f"atrim=0:{ACTION:g}", f"atrim=0:{TOTAL:g}") == _graph_of(held)
