from dataclasses import replace
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid


def _plan(*, missing: str | None = None, lead_padded: str | None = None) -> mp4_grid.GridStagePlan:
    labels = ["Anders", "Erik", "Johan", "Mathias"]
    tiles = []
    for index, label in enumerate(labels):
        row, col = divmod(index, 2)
        tiles.append(
            mp4_grid.GridTile(
                label=label,
                trim_path=None if label == missing else Path(f"/trims/{label}.mp4"),
                beep_offset_in_clip=2.0,
                seek_seconds=1.0,
                lead_pad_seconds=0.5 if label == lead_padded else 0.0,
                row=row,
                col=col,
            )
        )
    return mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tuple(tiles),
        duration_seconds=12.5,
        audio_label="Mathias",
        rows=2,
        cols=2,
    )


def _plan_1x3() -> mp4_grid.GridStagePlan:
    """Three shooters in one row. rows != cols, so a transposed offset shows."""
    labels = ["Anders", "Erik", "Johan"]
    tiles = tuple(
        mp4_grid.GridTile(
            label=label,
            trim_path=Path(f"/trims/{label}.mp4"),
            beep_offset_in_clip=2.0,
            seek_seconds=1.0,
            lead_pad_seconds=0.0,
            row=0,
            col=index,
        )
        for index, label in enumerate(labels)
    )
    return mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tiles,
        duration_seconds=12.5,
        audio_label="Anders",
        rows=1,
        cols=3,
    )


def _graph(cmd: tuple[str, ...]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_every_tile_is_scaled_padded_and_xstacked():
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/stage1.mov")
    )
    graph = _graph(cmd)
    # 3840x2160 canvas, 2x2 -> 1920x1080 cells.
    assert graph.count("scale=1920:1080:force_original_aspect_ratio=decrease") == 4
    assert graph.count("pad=1920:1080") == 4
    assert "xstack=inputs=4:layout=0_0|1920_0|0_1080|1920_1080" in graph


def test_a_non_square_grid_is_not_transposed():
    # A 2x2 grid is symmetric, so a swapped row/col is invisible there.
    # One row of three is not: transposing puts every tile at x=0 and
    # stacks them off the bottom of the canvas.
    graph = _graph(
        mp4_grid.build_stage_command(_plan_1x3(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    )
    assert graph.count("scale=1280:2160:force_original_aspect_ratio=decrease") == 3
    assert "xstack=inputs=3:layout=0_0|1280_0|2560_0" in graph


def test_tiles_are_sar_normalised_before_stacking():
    # xstack refuses inputs whose sample aspect ratios disagree, which is
    # exactly what mixed head-cam sources give you.
    graph = _graph(
        mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    )
    assert graph.count("setsar=1") == 4


def test_audio_track_per_shooter_in_alphabetical_order():
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/stage1.mov")
    )
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    # One composited video, then one audio label per shooter.
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]", "[a3]"]
    # Mathias is index 3 alphabetically and is the audio source.
    assert "-disposition:a:3" in cmd
    assert cmd[cmd.index("-disposition:a:3") + 1] == "default"


def test_only_the_audio_source_track_plays_by_default():
    # Every track default is as wrong as none: the player picks one
    # arbitrarily and the grid can come out with the wrong shooter's audio.
    cmd = mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    dispositions = [cmd[cmd.index(f"-disposition:a:{slot}") + 1] for slot in range(4)]
    assert dispositions == ["0", "0", "0", "default"]


def test_an_audio_label_matching_no_tile_is_named_not_a_bare_stopiteration():
    plan = replace(_plan(), audio_label="Nobody")
    with pytest.raises(ValueError, match="Nobody"):
        mp4_grid.build_stage_command(plan, canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))


def test_each_audio_track_is_named_after_its_shooter():
    # MP4 has no per-track title box: `title=` alone writes nothing a
    # player can show (checked against ffmpeg 7.0.2 -- the tracks come
    # back out as plain "SoundHandler"). handler_name is what lands.
    cmd = mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    named = [cmd[i + 1] for i, a in enumerate(cmd) if a.startswith("-metadata:s:a:")]
    assert named == [
        "title=Anders",
        "handler_name=Anders",
        "title=Erik",
        "handler_name=Erik",
        "title=Johan",
        "handler_name=Johan",
        "title=Mathias",
        "handler_name=Mathias",
    ]


def test_audio_tracks_are_normalised_to_one_sample_format_and_layout():
    # A mono trim next to the stereo anullsrc filler puts differently
    # shaped tracks in the same slot across segments, which is the audio
    # half of the concat -c copy invariant.
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan(missing="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
        )
    )
    assert graph.count("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo") == 4


def test_every_audio_track_spans_the_whole_stage():
    # The segment's streams have to end together, or concat -c copy
    # accumulates drift across stages.
    graph = _graph(
        mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    )
    assert graph.count("apad,atrim=0:12.5") == 4


def test_missing_trim_still_contributes_video_and_a_silent_audio_track():
    cmd = mp4_grid.build_stage_command(
        _plan(missing="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/s.mp4")
    )
    joined = " ".join(cmd)
    assert "color=c=black:s=1920x1080" in joined
    assert "anullsrc" in joined
    # The stream layout must not change: still four audio maps.
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]", "[a3]"]
    # And Erik's trim is not an input.
    assert "/trims/Erik.mp4" not in joined


def test_a_filler_tile_runs_the_whole_stage_at_the_canvas_rate():
    # Short filler would end the segment early on the black cell and pull
    # the concat stitch out of alignment.
    cmd = mp4_grid.build_stage_command(
        _plan(missing="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
    )
    joined = " ".join(cmd)
    assert "-f lavfi -t 12.5 -i color=c=black:s=1920x1080:r=30000/1001" in joined
    assert "-f lavfi -t 12.5 -i anullsrc=channel_layout=stereo:sample_rate=48000" in joined


def test_a_filler_tile_shifts_the_input_indices_of_the_tiles_behind_it():
    # Erik's filler burns two input slots (color + anullsrc), so Johan and
    # Mathias are inputs 3 and 4 -- not 2 and 3. Indexing the graph by tile
    # slot instead of by input would hand Johan Erik's silent track and
    # drop Mathias' audio entirely.
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan(missing="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
        )
    )
    assert "[0:v]" in graph and "[0:a]" in graph  # Anders
    assert "[1:v]" in graph and "[2:a]" in graph  # Erik's color + anullsrc
    assert "[3:v]" in graph and "[3:a]" in graph  # Johan
    assert "[4:v]" in graph and "[4:a]" in graph  # Mathias
    # The two indices the filler does not own must not be referenced.
    assert "[2:v]" not in graph and "[1:a]" not in graph


# --- cells the roster does not reach -------------------------------------
#
# A roster of 3 fills a 2x2 grid three quarters of the way. xstack's
# default ``fill=none`` leaves the fourth quadrant as raw frame buffer,
# which decodes as RGB(0,135,0) -- bright green, at every timestamp
# (measured with ffmpeg 6.1.1). And a roster of 6 in a 3x3 leaves xstack
# with extents of only two rows, so the render silently shrinks below the
# canvas. Both are fixed by giving every unreached cell the same black
# filler a missing trim already gets.


def _partial_plan(*, roster: int, rows: int, cols: int) -> mp4_grid.GridStagePlan:
    """``roster`` tiles laid row-major into a ``rows``x``cols`` grid."""
    labels = [f"Shooter{index}" for index in range(roster)]
    tiles = tuple(
        mp4_grid.GridTile(
            label=label,
            trim_path=Path(f"/trims/{label}.mp4"),
            beep_offset_in_clip=2.0,
            seek_seconds=1.0,
            lead_pad_seconds=0.0,
            row=index // cols,
            col=index % cols,
        )
        for index, label in enumerate(labels)
    )
    return mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tiles,
        duration_seconds=12.5,
        audio_label=labels[0],
        rows=rows,
        cols=cols,
    )


def test_an_unreached_cell_is_filled_with_black_rather_than_left_to_xstack():
    cmd = mp4_grid.build_stage_command(
        _partial_plan(roster=3, rows=2, cols=2),
        canvas=mp4_grid.GridCanvas(),
        output_path=Path("/o.mp4"),
    )
    graph = _graph(cmd)
    # Four cells stacked, not three -- the fourth is the filler.
    assert "xstack=inputs=4:layout=0_0|1920_0|0_1080|1920_1080" in graph
    # And it is a real black source, not xstack's fill= option (which
    # needs ffmpeg >= 5.1 and this repo pins no floor).
    assert " ".join(cmd).count("color=c=black:s=1920x1080:r=30000/1001") == 1
    assert "fill=" not in graph


def test_an_unreached_cell_adds_no_audio_track():
    # An empty cell is not a shooter. Giving it a track would change the
    # stream count away from the roster size and break concat -c copy.
    cmd = mp4_grid.build_stage_command(
        _partial_plan(roster=3, rows=2, cols=2),
        canvas=mp4_grid.GridCanvas(),
        output_path=Path("/o.mp4"),
    )
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]"]
    assert "anullsrc" not in " ".join(cmd)
    named = [cmd[i + 1] for i, a in enumerate(cmd) if a.startswith("-metadata:s:a:")]
    assert named == [
        "title=Shooter0",
        "handler_name=Shooter0",
        "title=Shooter1",
        "handler_name=Shooter1",
        "title=Shooter2",
        "handler_name=Shooter2",
    ]


def test_a_partly_filled_grid_still_stacks_out_to_the_whole_canvas():
    # Six shooters in a 3x3: without the bottom row's fillers xstack's
    # extents are 3840x1440 and the render comes out short of the canvas.
    graph = _graph(
        mp4_grid.build_stage_command(
            _partial_plan(roster=6, rows=3, cols=3),
            canvas=mp4_grid.GridCanvas(),
            output_path=Path("/o.mp4"),
        )
    )
    assert "xstack=inputs=9:" in graph
    layout = graph.split("xstack=inputs=9:layout=")[1].split("[")[0]
    offsets = layout.split("|")
    assert offsets[6:] == ["0_1440", "1280_1440", "2560_1440"]


def test_a_filler_tile_and_an_unreached_cell_can_coexist():
    # A three-shooter roster where one has no trim: three colour sources
    # (two fillers' worth of cell plus the empty quadrant) but only one
    # silent audio track, because only the shooter gets one.
    plan = _partial_plan(roster=3, rows=2, cols=2)
    tiles = list(plan.tiles)
    tiles[1] = replace(tiles[1], trim_path=None)
    cmd = mp4_grid.build_stage_command(
        replace(plan, tiles=tuple(tiles)),
        canvas=mp4_grid.GridCanvas(),
        output_path=Path("/o.mp4"),
    )
    joined = " ".join(cmd)
    assert joined.count("color=c=black:s=1920x1080") == 2
    assert joined.count("anullsrc") == 1
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]"]


def test_a_full_grid_adds_no_filler_inputs():
    cmd = mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    assert "color=c=black" not in " ".join(cmd)
    assert "xstack=inputs=4:" in _graph(cmd)


def test_seek_and_duration_are_applied_before_each_input():
    cmd = mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/s.mp4"))
    first_input = cmd.index("-i")
    assert cmd[first_input - 4] == "-ss"
    assert cmd[first_input - 3] == "1"
    assert cmd[first_input - 2] == "-t"
    assert cmd[first_input - 1] == "12.5"


def test_output_frame_rate_is_pinned_for_concat_compatibility():
    canvas = mp4_grid.GridCanvas(frame_rate_num=30000, frame_rate_den=1001)
    cmd = mp4_grid.build_stage_command(_plan(), canvas=canvas, output_path=Path("/out/s.mp4"))
    assert "-r" in cmd
    assert cmd[cmd.index("-r") + 1] == "30000/1001"


def test_a_stage_segment_carries_pcm_so_the_stitch_has_nothing_to_accumulate():
    # AAC cannot encode an arbitrary length exactly, and its priming and
    # padding do not compose across the concat demuxer -- they arrive at
    # the stitch as real samples, ~30ms of them per stage, all pushing
    # audio later against picture. PCM has neither.
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/stage1.mov")
    )
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert "aac" not in cmd
    assert "-b:a" not in cmd
    # Nothing streams an intermediate, so faststart's second pass over a
    # multi-gigabyte segment buys nothing.
    assert "-movflags" not in cmd


def test_a_lead_padded_tile_is_front_padded_so_its_beep_still_lands_on_time():
    # Erik's beep sits closer to his clip start than head_pad, so 0.5s of
    # pad has to be synthesised. Without it his beep lands 0.5s early and
    # the grid is desynced -- the failure the grid exists to prevent.
    cmd = mp4_grid.build_stage_command(
        _plan(lead_padded="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
    )
    graph = _graph(cmd)
    assert "tpad=start_duration=0.5:start_mode=add:color=black" in graph
    assert "adelay=500:all=1" in graph
    # Only Erik is padded; the other three tiles are untouched.
    assert graph.count("tpad=start_duration") == 1
    assert graph.count("adelay=") == 1
    # Erik's input reads 0.5s less, since the pad supplies the remainder.
    joined = " ".join(cmd)
    assert "-t 12 -i /trims/Erik.mp4" in joined
    assert "-t 12.5 -i /trims/Anders.mp4" in joined


def test_the_lead_pad_is_applied_to_the_padded_tiles_own_chain():
    # A pad on the wrong tile is worse than no pad: it desyncs a tile that
    # was already aligned and leaves the clamped one early.
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan(lead_padded="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
        )
    )
    erik_video = next(part for part in graph.split(";") if part.endswith("[t1]"))
    erik_audio = next(part for part in graph.split(";") if part.endswith("[a1]"))
    assert "tpad=start_duration=0.5" in erik_video
    assert "adelay=500:all=1" in erik_audio


def test_the_lead_pad_comes_before_setpts_or_ffmpeg_swallows_it():
    # Measured on ffmpeg 7.0.2: with setpts=PTS-STARTPTS ahead of it, a
    # 2.5s input asked for 0.5s of head pad came out 2.52s -- the pad is
    # dropped without a word and the tile's beep lands early again. The
    # ordering is the fix, so pin it.
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan(lead_padded="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
        )
    )
    erik_video = next(part for part in graph.split(";") if part.endswith("[t1]"))
    assert erik_video.index("tpad=") < erik_video.index("setpts=")


def test_tiles_without_a_lead_pad_emit_no_head_padding_filters():
    # The tail pad is unconditional, so this is specifically about the
    # head: a tile that needs no lead pad must not get one.
    graph = _graph(
        mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    )
    assert "tpad=start_duration" not in graph
    assert "adelay=" not in graph


def test_every_tile_is_tail_padded_to_the_stage_duration():
    # A tile's content is head_pad + its own post-beep span, while the
    # stage runs head_pad + the longest post-beep span + tail_pad, so
    # even the longest tile falls exactly one tail pad short. Left alone,
    # the segment's video ends before its audio on every filler-free
    # stage and concat -c copy carries the gap into every later stage.
    graph = _graph(
        mp4_grid.build_stage_command(_plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4"))
    )
    for slot in range(4):
        chain = next(part for part in graph.split(";") if part.endswith(f"[t{slot}]"))
        assert "tpad=stop_duration=12.5:stop_mode=add:color=black" in chain
        assert chain.endswith(f"trim=0:12.5[t{slot}]")
        # Pad first, then cut back: trimming before the pad would trim
        # footage that is already too short and change nothing.
        assert chain.index("tpad=stop_duration") < chain.index("trim=0:")
    # Which is the length the audio side is already held to.
    assert graph.count("atrim=0:12.5") == 4


def test_concat_copies_the_video_and_encodes_the_audio_once():
    # Video is copied -- every segment was pinned to the same canvas and
    # rate precisely so it can be. Audio is not: the segments carry PCM,
    # and one encode over the whole match is what keeps per-segment AAC
    # priming from accumulating into audible A/V drift.
    cmd = mp4_grid.build_concat_command(list_path=Path("/tmp/list.txt"), output_path=Path("/out/grid.mp4"))
    assert cmd[-1] == "/out/grid.mp4"
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    # A blanket "-c copy" would put the segments' PCM straight into the
    # MP4, which is the container that does not officially carry it.
    assert "-c" not in cmd
    assert "concat" in cmd


def test_concat_keeps_every_audio_track():
    # Without -map 0 ffmpeg's default stream selection keeps one stream
    # per type, so a four-shooter stitch comes out with a single audio
    # track (verified against ffmpeg 7.0.2). That loses the whole
    # per-shooter audio feature silently, at the last step, after every
    # stage has already been encoded.
    cmd = mp4_grid.build_concat_command(list_path=Path("/tmp/list.txt"), output_path=Path("/out/grid.mp4"))
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "0"
    # The mapping has to be an output option, i.e. after the input.
    assert cmd.index("-map") > cmd.index("-i")


def test_concat_restores_the_track_names_and_the_default_track():
    # Stream copy does not carry either across the concat demuxer: the
    # muxer re-derives the default flag onto the first audio track, so
    # the stitched file would play the alphabetically-first shooter
    # instead of the audio source.
    cmd = mp4_grid.build_concat_command(
        list_path=Path("/tmp/list.txt"),
        output_path=Path("/out/grid.mp4"),
        audio_labels=["Anders", "Erik", "Johan", "Mathias"],
        default_audio_label="Mathias",
    )
    dispositions = [cmd[cmd.index(f"-disposition:a:{slot}") + 1] for slot in range(4)]
    assert dispositions == ["0", "0", "0", "default"]
    assert "handler_name=Erik" in cmd


def test_concat_rejects_a_default_label_outside_the_roster():
    with pytest.raises(ValueError, match="Nobody"):
        mp4_grid.build_concat_command(
            list_path=Path("/tmp/list.txt"),
            output_path=Path("/out/grid.mp4"),
            audio_labels=["Anders"],
            default_audio_label="Nobody",
        )
