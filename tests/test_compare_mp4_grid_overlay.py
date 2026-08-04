"""The overlay half of the grid's filter graph.

Kept apart from the three existing grid test files so a regression in
the no-overlay path stays unambiguous.

Two assertions differ from the task brief on purpose. The brief asserted
``graph.endswith("[grid]format=yuv420p[final]")``; the graph has always
ended with the ``amix`` chain, because the audio half is appended after
the video half. Reordering it to satisfy the brief would change the
no-overlay filter string, which is exactly what this task must not do,
so the assertion is ``in`` rather than ``endswith`` and the ordering is
pinned separately by :func:`test_the_video_chain_ends_at_format_yuv420p`.
"""

import subprocess
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.mp4_grid import GridCanvas, GridStagePlan, GridTile
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle

CANVAS = GridCanvas(width=1920, height=1080, frame_rate_num=60000, frame_rate_den=1001)


def _tile(label, row, col, *, present=True):
    return GridTile(
        label=label,
        trim_path=Path(f"/tmp/{label}.mov") if present else None,
        beep_offset_in_clip=1.0,
        seek_seconds=0.0,
        lead_pad_seconds=0.0,
        row=row,
        col=col,
    )


def _plan(tiles, *, rows=2, cols=2, duration=10.0):
    return GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tuple(tiles),
        duration_seconds=duration,
        audio_label=tiles[0].label,
        rows=rows,
        cols=cols,
    )


def _overlay(tmp_path, clocks=()):
    list_path = tmp_path / "sprites.txt"
    list_path.write_text("file '/tmp/a.png'\nduration 10\nfile '/tmp/a.png'\n")
    return mp4_grid.StageOverlayPlan(
        sprite_list_path=list_path,
        font_path=tmp_path / "font.ttf",
        font_size=64,
        clocks=tuple(clocks),
    )


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _video_parts(graph):
    """The video half: everything up to and including ``[final]``.

    The audio chains are appended after it, so ``endswith`` on the whole
    graph cannot see the video tail.
    """
    parts = graph.split(";")
    return parts[: [i for i, p in enumerate(parts) if p.endswith("[final]")][0] + 1]


def test_without_overlay_the_graph_is_untouched(tmp_path):
    cmd = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)]),
        canvas=CANVAS,
        output_path=tmp_path / "out.mov",
    )
    graph = _graph(cmd)
    assert "overlay=" not in graph
    assert "drawtext" not in graph
    assert "[grid]format=yuv420p[final]" in graph
    assert "-f" not in cmd[cmd.index("-filter_complex") :]


def test_the_video_chain_ends_at_format_yuv420p(tmp_path):
    """``[final]`` terminates the video half, with the overlay on or off.

    Asserts the partition, not just that the part named ``[final]`` is
    named ``[final]``: exactly one node produces it, it converts to
    yuv420p, everything before it is video and everything after it is
    audio.
    """
    for overlay in (None, _overlay(tmp_path)):
        graph = _graph(
            mp4_grid.build_stage_command(
                _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)]),
                canvas=CANVAS,
                output_path=tmp_path / "o.mov",
                overlay=overlay,
            )
        )
        parts = graph.split(";")
        finals = [i for i, p in enumerate(parts) if p.endswith("[final]")]
        assert len(finals) == 1
        cut = finals[0]
        assert parts[cut].endswith("format=yuv420p[final]")
        assert all(":a]" not in p and "amix" not in p for p in parts[:cut])
        assert parts[cut + 1 :] and all(":a]" in p or "amix" in p for p in parts[cut + 1 :])


def test_overlay_defaults_to_off(tmp_path):
    plain = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]), canvas=CANVAS, output_path=tmp_path / "o.mov"
    )
    explicit = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]),
        canvas=CANVAS,
        output_path=tmp_path / "o.mov",
        overlay=None,
    )
    assert plain == explicit


def test_sprite_input_is_appended_after_every_other_input(tmp_path):
    # A filler tile takes two inputs and an unreached cell takes one, so
    # the sprite must land last or every index behind it shifts.
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1, present=False), _tile("cy", 1, 0)])
    cmd = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )
    inputs = [i for i, a in enumerate(cmd) if a == "-i"]
    assert cmd[inputs[-1] + 1] == str(tmp_path / "sprites.txt")
    assert cmd[inputs[-1] - 4 : inputs[-1]] == ("-f", "concat", "-safe", "0")
    # ann(1) + bo filler(2) + cy(1) + one unreached cell(1) + sprite(1).
    assert len(inputs) == 6


def test_the_sprite_stream_index_is_the_last_one(tmp_path):
    """The graph must read the sprite from the index the argv gave it.

    Off-by-one here is the failure the ordering rule exists to prevent:
    the graph would take a shooter's video as the overlay and that
    shooter's audio would end up under someone else's label.
    """
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1, present=False), _tile("cy", 1, 0)])
    cmd = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )
    inputs = [i for i, a in enumerate(cmd) if a == "-i"]
    sprite_stream = len(inputs) - 1
    graph = _graph(cmd)
    chain = next(p for p in graph.split(";") if p.endswith("[ovl]"))
    assert chain.startswith(f"[{sprite_stream}:v]")
    # ...and nothing else in the graph claims that index.
    assert f"[{sprite_stream}:a]" not in graph
    assert graph.count(f"[{sprite_stream}:v]") == 1


def test_sprite_input_uses_the_concat_demuxer(tmp_path):
    cmd = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]),
        canvas=CANVAS,
        output_path=tmp_path / "o.mov",
        overlay=_overlay(tmp_path),
    )
    joined = " ".join(cmd)
    assert "-f concat -safe 0 -i" in joined


def test_tile_input_indices_do_not_move_when_the_overlay_is_added(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1, present=False), _tile("cy", 1, 0)])
    plain = _graph(mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov"))
    with_overlay = _graph(
        mp4_grid.build_stage_command(
            plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
        )
    )
    tile_chains = [p for p in plain.split(";") if p.endswith(("[t0]", "[t1]", "[t2]"))]
    assert len(tile_chains) == 3
    for chain in tile_chains:
        assert chain in with_overlay, f"tile chain changed: {chain}"


def test_the_beep_alignment_filter_order_survives_the_overlay(tmp_path):
    """Invariant 2: ``tpad`` before ``setpts``, and nothing between them.

    This broke once by reordering. The overlay composites after
    ``xstack``, so a tile chain that has moved at all is a defect.
    """
    tile = GridTile(
        label="ann",
        trim_path=Path("/tmp/ann.mov"),
        beep_offset_in_clip=0.2,
        seek_seconds=0.0,
        lead_pad_seconds=0.8,
        row=0,
        col=0,
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([tile]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    chain = next(p for p in graph.split(";") if p.endswith("[t0]"))
    assert chain.startswith("[0:v]tpad=start_duration=0.8:start_mode=add:color=black,setpts=PTS-STARTPTS,")
    assert chain.endswith("trim=0:10[t0]")


def test_audio_graph_is_identical_with_the_overlay_on(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)])
    plain = _graph(mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov"))
    with_overlay = _graph(
        mp4_grid.build_stage_command(
            plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
        )
    )

    def audio_of(graph):
        return [p for p in graph.split(";") if (p.startswith("[") and ":a]" in p) or "amix" in p]

    assert audio_of(plain) == audio_of(with_overlay)
    assert len(audio_of(plain)) == 3  # two tiles + the mix


def test_track_count_and_maps_are_unchanged_with_the_overlay_on(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)])
    plain = mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov")
    with_overlay = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )

    def maps(cmd):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]

    assert maps(plain) == maps(with_overlay)
    assert maps(plain) == ["[final]", "[amix]", "[a0]", "[a1]"]


def test_the_overlay_leaves_the_stream_layout_and_codecs_alone(tmp_path):
    """Invariants 1, 3 and 4: everything after ``-filter_complex``.

    The stitch's ``-c copy`` refuses segments that disagree on any of
    it, and it refuses at the very last step.
    """
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)])
    plain = mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov")
    with_overlay = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )
    tail = plain[plain.index("-filter_complex") + 2 :]
    assert tail == with_overlay[with_overlay.index("-filter_complex") + 2 :]
    assert "handler_name=ann" in tail
    assert mp4_grid.SEGMENT_AUDIO_CODEC in tail


def test_sprite_chain_is_rgba_and_covers_the_whole_stage(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    chain = next(p for p in graph.split(";") if p.endswith("[ovl]"))
    assert "format=rgba" in chain
    assert "stop_mode=clone" in chain
    assert "trim=0:10" in chain
    assert "fps=60000/1001" in chain


def test_overlay_composites_onto_the_stacked_grid_then_converts(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    assert "[grid][ovl]overlay=0:0" in graph
    assert _video_parts(graph)[-1].endswith("format=yuv420p[final]")
    assert graph.index("xstack") < graph.index("overlay=0:0")


def test_a_clock_is_drawn_for_each_tile_that_has_one(tmp_path):
    clocks = (
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),
        mp4_grid.TileClock(row=0, col=1, start_seconds=1.0, freeze_seconds=None, final_text=None),
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    # ann: ticking + static hold. bo: ticking only.
    assert graph.count("drawtext") == 3
    assert "5.00" in graph


def test_the_clocks_hang_off_the_composited_grid_not_the_bare_one(tmp_path):
    clocks = (mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),)
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    assert graph.index("overlay=0:0") < graph.index("drawtext")
    drawn = next(p for p in graph.split(";") if "drawtext" in p)
    assert drawn.startswith("[ovlgrid]")


def test_no_clocks_means_no_drawtext(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    assert "drawtext" not in graph


def test_the_ticking_clock_stops_where_the_static_one_starts(tmp_path):
    clocks = (mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),)
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    # The ticking filter stops strictly below the freeze and the hold
    # starts at it, so no frame can ever draw both.
    assert r"enable='gte(t\,1)*lt(t\,6)'" in graph
    assert r"enable='gte(t\,6)'" in graph
    # The same freeze time bounds both filters -- not two different ones.
    assert graph.count(r"lt(t\,6)") == 1
    assert graph.count(r"gte(t\,6)") == 1
    assert "between(t" not in graph


def test_an_open_ended_clock_still_waits_for_the_beep(tmp_path):
    """No freeze does not mean no lower bound.

    Without ``gte(t,start)`` the filter runs from frame zero and
    ``t - start`` is negative through the head pad, so the clock renders
    a negative elapsed time for a run that has not begun (measured:
    ``-1.00`` at t=0, ``0.50`` at t=0.5 for a start of 1.5).
    """
    clocks = (mp4_grid.TileClock(row=0, col=0, start_seconds=1.5, freeze_seconds=None, final_text=None),)
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    assert graph.count("drawtext") == 1
    assert r"trunc(t-1.5)" in graph
    assert r"enable='gte(t\,1.5)'" in graph
    # No end, so nothing bounds it from above.
    assert "lt(t" not in graph
    assert "between(t" not in graph


def test_clock_is_positioned_inside_its_own_cell(tmp_path):
    clocks = (mp4_grid.TileClock(row=1, col=1, start_seconds=1.0, freeze_seconds=None, final_text=None),)
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0), _tile("bo", 0, 1), _tile("cy", 1, 0), _tile("dee", 1, 1)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    # Cells are 960x540 on a 1920x1080 canvas, so the bottom-right cell
    # starts at x=960, y=540; the inset is max(24, 540 // 36) = 24. ``tw``
    # is drawtext's own text width, so the right edge costs no measuring.
    drawn = next(p for p in graph.split(";") if "drawtext" in p)
    assert ":x=960+960-tw-24:y=540+24:" in drawn


def test_the_clock_uses_the_font_size_and_colours_the_plan_names(tmp_path):
    clocks = (mp4_grid.TileClock(row=0, col=0, start_seconds=0.0, freeze_seconds=None, final_text=None),)
    overlay = mp4_grid.StageOverlayPlan(
        sprite_list_path=_overlay(tmp_path).sprite_list_path,
        font_path=tmp_path / "font.ttf",
        font_size=64,
        clocks=clocks,
        ink=(244, 244, 245),
        stroke=(10, 11, 13),
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=overlay,
        )
    )
    assert f"fontfile='{overlay.font_path}'" in graph
    assert "fontsize=64" in graph
    # Hex, not a name: ffmpeg only knows colours from its own table and
    # has no name for (244, 244, 245).
    assert "fontcolor=0xf4f4f5" in graph
    assert "bordercolor=0x0a0b0d" in graph


def test_the_clock_colours_default_to_white_on_black(tmp_path):
    """A caller with no theme to hand still gets a legible clock."""
    clocks = (mp4_grid.TileClock(row=0, col=0, start_seconds=0.0, freeze_seconds=None, final_text=None),)
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    assert "fontcolor=0xffffff" in graph
    assert "bordercolor=0x000000" in graph


def test_the_render_gives_the_clock_the_themes_own_colours(tmp_path):
    """The clock and the sprite text beside it must not differ in colour."""
    from splitsmith.overlay_theme import load_theme

    calls, runner = _recorder()
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        overlay=True,
    )
    theme = load_theme("splitsmith")
    graph = _graph(calls[0])
    assert f"fontcolor=0x{theme.ink[0]:02x}{theme.ink[1]:02x}{theme.ink[2]:02x}" in graph
    assert f"bordercolor=0x{theme.stroke[0]:02x}{theme.stroke[1]:02x}{theme.stroke[2]:02x}" in graph


def test_the_held_text_is_truncated_not_rounded(tmp_path):
    """The hold must never read above the last value the clock ticked."""
    assert mp4_grid._clock_text(1.958) == "1.95"  # truncated, not 1.96
    assert mp4_grid._clock_text(5.0) == "5.00"
    assert mp4_grid._clock_text(0.05) == "0.05"
    assert mp4_grid._clock_text(12.999) == "12.99"


def test_the_held_text_truncates_on_milliseconds_not_in_floating_point(tmp_path):
    """``math.floor(x * 100) / 100`` is wrong for ordinary split times.

    ``0.29 * 100`` is ``28.999999999999996`` and ``1.13 * 100`` is
    ``112.99999999999999``, so flooring the product drops a hundredth and
    the clock reads low at the freeze. Both are perfectly ordinary shot
    times, so this is not a corner case. Truncation runs on integer
    milliseconds instead.

    (The reviewer's example, ``2.09``, happens to be exact on this
    platform -- ``2.09 * 100 == 209.0`` -- which is why the value matters
    here and a plausible-looking one proves nothing.)
    """
    assert mp4_grid._clock_text(0.29) == "0.29"
    assert mp4_grid._clock_text(1.13) == "1.13"


# --- the graph ffmpeg actually accepts ------------------------------------


FFMPEG_MISSING = subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG_MISSING, reason="needs a real ffmpeg on PATH")
def test_ffmpeg_parses_the_clock_filters(tmp_path):
    """A string test cannot see ``drawtext`` escaping. ffmpeg can.

    ``-f null`` over a two-frame lavfi source: this proves the filter
    description parses and runs, which is the half of the clock no unit
    test reaches. Costs milliseconds and needs no media.
    """
    from splitsmith.overlay_text import materialize_font

    font = materialize_font("splitsmith-mono", tmp_path)
    clocks = (
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=3.0, final_text="2.00"),
        mp4_grid.TileClock(row=1, col=1, start_seconds=1.0, freeze_seconds=None, final_text=None),
    )
    overlay = mp4_grid.StageOverlayPlan(
        sprite_list_path=tmp_path / "unused.txt",
        font_path=font,
        font_size=48,
        clocks=clocks,
    )
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 1, 1)])
    graph = ";".join(mp4_grid._clock_filters(plan, CANVAS, overlay))
    done = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-t", "0.1", "-i", "color=c=gray:s=1920x1080:r=30",
            "-filter_complex", graph.replace("[ovlgrid]", "[0:v]"),
            "-map", "[final]", "-f", "null", "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-3000:]


# --- render driver wiring -------------------------------------------------


def _stage_bundle(n: int, tmp_path: Path, label: str) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=tmp_path / f"{label}-{n}.mov",
        audit_path=tmp_path / f"{label}-{n}.json",
        beep_offset_in_clip=2.0,
        duration_seconds=12.0,
        width=1920,
        height=1080,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _audit(path: Path, times: list[float]) -> None:
    """An audit file in the real on-disk shape: ``ms_after_beep``, not seconds.

    The task brief's fixture used a ``time_seconds`` key, which
    ``audit_shots_to_engine_shots`` ignores -- every tile came back with
    no shots and the clock assertions could not fail for the right
    reason.
    """
    import json

    path.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": round(t * 1000)}
                    for i, t in enumerate(times)
                ]
            }
        ),
        encoding="utf-8",
    )


def _shooters(tmp_path: Path, *, shots: dict[str, list[float]] | None = None):
    shots = shots or {"Anders": [0.9, 1.4, 2.1], "Mathias": [1.0, 1.6, 2.4]}
    bundles = []
    for label, times in shots.items():
        stage = _stage_bundle(1, tmp_path, label)
        _audit(stage.audit_path, times)
        bundles.append(
            CompareShooterBundle(
                label=label,
                project_root=tmp_path / label,
                stages_by_number={1: stage},
            )
        )
    return bundles


def _recorder():
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return calls, runner


def test_render_without_overlay_writes_no_sprites(tmp_path):
    calls, runner = _recorder()
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
    )
    assert not (work / "sprites").exists()
    assert list(work.glob("sprites-stage*.txt")) == []
    assert not any("overlay=0:0" in " ".join(cmd) for cmd in calls)


def test_render_with_overlay_writes_sprites_and_uses_them(tmp_path):
    calls, runner = _recorder()
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
        overlay=True,
    )
    list_path = work / "sprites-stage1.txt"
    assert list_path.exists()
    sprites = sorted((work / "sprites").glob("*.png"))
    assert sprites, "no sprite PNGs were rendered"
    stage_cmd = calls[0]
    assert str(list_path) in stage_cmd
    assert "overlay=0:0" in " ".join(stage_cmd)
    # The font must be a real file drawtext can open.
    graph = _graph(stage_cmd)
    font = graph.split("fontfile='")[1].split("'")[0]
    assert Path(font).is_file()


def test_the_sprite_list_is_written_at_the_canvas_frame_rate(tmp_path):
    """The list writer gets the *output* rate, not a guess.

    Both halves of what the writer does need it: the ``option framerate``
    directive that stops the concat demuxer taking image2's default 25fps
    as its time base, and the quantisation that puts every state boundary
    on a frame that exists. A hardcoded rate produces a list that decodes
    fine and steps the sprite a frame away from the clock.
    """
    calls, runner = _recorder()
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,  # 60000/1001, deliberately not a round rate
        runner=runner,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
        overlay=True,
        head_pad_seconds=1.0,
    )
    lines = [ln for ln in (work / "sprites-stage1.txt").read_text().splitlines() if ln.strip()]
    options = {ln for ln in lines if ln.startswith("option ")}
    assert options == {"option framerate 60000/1001"}, options

    elapsed = 0.0
    for line in lines:
        if not line.startswith("duration "):
            continue
        frames = elapsed * CANVAS.frame_rate_num / CANVAS.frame_rate_den
        assert abs(frames - round(frames)) < 1e-6, f"boundary {elapsed} is not on a canvas frame"
        elapsed += float(line.split()[1])


def test_the_stage_slice_reaches_the_sprites_rather_than_blanking_them(tmp_path):
    """``load_overlay_data`` is keyed by ``(label, stage)``; the sprite
    builder is keyed by label. Passing the wrong one through blanks every
    panel with no crash and no warning, so assert on the panels."""
    from splitsmith.compare.overlay_data import load_overlay_data
    from splitsmith.compare.overlay_sprites import TilePlacement, build_overlay_states

    shooters = _shooters(tmp_path)
    data = load_overlay_data(shooters)
    assert set(data) == {("Anders", 1), ("Mathias", 1)}

    placements = (
        TilePlacement(label="Anders", row=0, col=0, present=True),
        TilePlacement(label="Mathias", row=0, col=1, present=True),
    )
    sliced = mp4_grid._overlay_data_for_stage(data, 1)
    assert set(sliced) == {"Anders", "Mathias"}
    states = build_overlay_states(placements, sliced, head_pad_seconds=1.0, duration_seconds=12.0)
    assert len(states) > 1, "no shot events reached the state builder"
    assert any(p.shots_fired > 0 for s in states for p in s.panels)
    assert any(p.last_split is not None for s in states for p in s.panels)
    assert any(p.rank is not None for s in states for p in s.panels)


def test_a_tuple_keyed_mapping_is_rejected_rather_than_blanking(tmp_path):
    from splitsmith.compare.overlay_data import load_overlay_data
    from splitsmith.compare.overlay_sprites import TilePlacement, build_overlay_states

    data = load_overlay_data(_shooters(tmp_path))
    with pytest.raises(ValueError, match="keyed by tile label"):
        build_overlay_states(
            (TilePlacement(label="Anders", row=0, col=0, present=True),),
            data,  # type: ignore[arg-type]
            head_pad_seconds=1.0,
            duration_seconds=12.0,
        )


def test_the_clock_freezes_at_the_shooters_last_shot(tmp_path):
    """The clock stops where the shooter stops, on the stage timeline."""
    calls, runner = _recorder()
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path, shots={"Anders": [0.9, 2.5], "Mathias": [1.0]}),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        overlay=True,
        head_pad_seconds=1.0,
    )
    graph = _graph(calls[0])
    # head_pad 1.0 + last shot 2.5 -> freeze at 3.5, holding "2.50".
    assert r"enable='gte(t\,3.5)'" in graph
    assert "text='2.50'" in graph
    # Mathias fired once, at 1.0 -> freeze at 2.0, holding "1.00".
    assert r"enable='gte(t\,2)'" in graph
    assert "text='1.00'" in graph
    assert graph.count("drawtext") == 4


def test_a_tile_with_no_shots_gets_no_clock(tmp_path):
    calls, runner = _recorder()
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path, shots={"Anders": [0.9, 2.5], "Mathias": []}),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        overlay=True,
    )
    graph = _graph(calls[0])
    assert graph.count("drawtext") == 2  # Anders only: ticking + hold


def test_the_head_pad_is_threaded_into_the_clock_not_hardcoded(tmp_path):
    calls, runner = _recorder()
    mp4_grid.render_grid_mp4(
        _shooters(tmp_path, shots={"Anders": [1.0]}),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        overlay=True,
        head_pad_seconds=2.5,
    )
    graph = _graph(calls[0])
    assert r"trunc(t-2.5)" in graph
    assert r"enable='gte(t\,2.5)*lt(t\,3.5)'" in graph


def _two_stage_shooters(tmp_path: Path):
    """One shooter, two stages, with *different* shot times per stage.

    Identical data across stages would let a wrong per-stage slice --
    ``_overlay_data_for_stage(data, 1)`` for every stage -- pass
    unnoticed.
    """
    shooters = _shooters(tmp_path, shots={"Anders": [0.9, 2.5]})
    stage2 = _stage_bundle(2, tmp_path, "Anders")
    _audit(stage2.audit_path, [1.2, 4.0])
    shooters[0].stages_by_number[2] = stage2
    return shooters


def test_each_stage_draws_its_own_shot_data_not_stage_ones(tmp_path):
    """The clock in stage 2 must come from stage 2's audit.

    ``load_overlay_data`` is keyed by ``(label, stage)``; slicing it on a
    fixed stage number rather than the plan's yields a graph that is
    perfectly well-formed and shows the wrong stage's times.
    """
    calls, runner = _recorder()
    mp4_grid.render_grid_mp4(
        _two_stage_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        overlay=True,
        head_pad_seconds=1.0,
    )
    stage1, stage2 = _graph(calls[0]), _graph(calls[1])
    # Stage 1's last shot is 2.5 -> freeze 3.5, holding "2.50".
    assert r"enable='gte(t\,3.5)'" in stage1
    assert "text='2.50'" in stage1
    # Stage 2's last shot is 4.0 -> freeze 5.0, holding "4.00".
    assert r"enable='gte(t\,5)'" in stage2
    assert "text='4.00'" in stage2
    # ...and stage 1's numbers must not appear in stage 2's graph.
    assert "text='2.50'" not in stage2
    assert r"gte(t\,3.5)" not in stage2


def test_the_sprite_cache_is_shared_across_stages(tmp_path):
    """One cache dir for the run, so an unchanged state is drawn once."""
    calls, runner = _recorder()
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _two_stage_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
        overlay=True,
    )
    assert (work / "sprites-stage1.txt").exists()
    assert (work / "sprites-stage2.txt").exists()
    assert len(list((work / "sprites").glob("*.png"))) >= 1
    assert len(calls) == 3  # two stages + the stitch
    # Each stage's command names its own list file, not the other's.
    assert str(work / "sprites-stage1.txt") in calls[0]
    assert str(work / "sprites-stage2.txt") in calls[1]
    # ...and carries its own stage's freeze, so a slice pinned to a fixed
    # stage number cannot hide behind a shared cache.
    assert "text='2.50'" in _graph(calls[0])
    assert "text='4.00'" in _graph(calls[1])
