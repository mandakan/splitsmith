"""Integration test: does ``--overlay`` reach the rendered pixels.

Task 6 wires ``--overlay`` onto ``splitsmith compare export --format
mp4``. The unit tests already prove the filter graph is built correctly
in isolation (``test_compare_mp4_grid_overlay.py``) and the CLI plumbing
tests prove the flag reaches ``render_grid_mp4``
(``test_compare_cli_mp4.py``). Neither proves a viewer would see
anything different -- a filter-graph string can be syntactically
perfect and still draw nothing, or draw over the wrong tile, or shift
the stream layout so the file plays silent in every non-NLE player.

So this test renders one stage twice, with and without ``--overlay``
(driving ``render_grid_mp4`` directly, the same layer the CLI calls),
decodes real frames from both outputs with ffmpeg, and diffs them with
PIL/numpy. A command-string assertion cannot tell you a viewer sees
anything; this can.

Roster is three shooters, not two or four. Three is the smallest roster
that both:

- leaves one cell of the 2x2 grid unreached, so "an empty cell is not a
  shooter" (``overlay_sprites._draw_panel``'s ``present`` guard) is
  exercised on the rendered path, not just in a unit test that
  constructs a ``TilePlacement(present=False)`` by hand; and
- lets one shooter go without an audit file, so the no-data
  degradation (``ui.exports.read_audit_data``'s missing-file branch)
  runs through the whole pipeline -- loader, sprite builder, filter
  graph, ffmpeg -- instead of only through ``test_compare_overlay_data.py``.

Every video-derived clip is built once by ``tests/synthetic_media.py``
(the shared ``synthetic_source_video`` session fixture) and read three
times as independent ffmpeg inputs -- there is no need for the three
tiles to look different from each other, since every assertion below
compares the *same* footage rendered with and without the overlay, not
the tiles against one another.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.overlay_theme import load_theme
from tests.synthetic_media import SYNTHETIC_FPS_DEN, SYNTHETIC_FPS_NUM

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

integration = pytest.mark.integration
needs_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="needs a real ffmpeg and ffprobe on PATH"
)

# A small, whole-number rate rather than the source's real 30000/1001:
# every tile chain applies its own ``fps=`` filter regardless of the
# source's rate (mp4_grid.py, ``setsar=1,fps={rate}``), so pinning the
# canvas to a round number keeps the "within one frame" duration
# tolerance a round number too, instead of a repeating fraction.
CANVAS = mp4_grid.GridCanvas(width=640, height=360, frame_rate_num=30, frame_rate_den=1)
FRAME_SECONDS = 1 / 30

HEAD_PAD_SECONDS = 1.0
TAIL_PAD_SECONDS = 0.5
BEEP_OFFSET_SECONDS = 3.0  # into the clip; seek clamps to beep - head_pad = 2.0s
STAGE_DURATION_SECONDS = 9.0  # per-shooter clip length handed to the bundle

# Segment layout: head pad (1.0s) + post-beep span (9.0 - 3.0 = 6.0s) +
# tail pad (0.5s) = 7.5s per the whole-driver stitch.
SEGMENT_SECONDS = HEAD_PAD_SECONDS + (STAGE_DURATION_SECONDS - BEEP_OFFSET_SECONDS) + TAIL_PAD_SECONDS

# Every tile's beep lands at the segment's head pad (1.0s -- see
# ``_stage_overlay_plan``'s comment on ``start_seconds``), so absolute
# segment time is always ``HEAD_PAD_SECONDS + seconds-from-beep``.
# Anders (row0,col0): shots at 1.0+0.5=1.5s and 1.0+3.0=4.0s; her clock
# freezes at head_pad + last_shot = 4.0s. Mathias (row1,col0): shots at
# 1.0+1.2=2.2s and 1.0+2.0=3.0s. T_AFTER_FIRST_SHOT below (2.0s) sits
# after Anders' first shot but before Mathias's -- nobody but Anders has
# any overlay content yet, a clean single-shooter-fired state with no
# confound from another tile changing at the same instant.
ANDERS_SHOTS_MS = [500, 3000]
MATHIAS_SHOTS_MS = [1200, 2000]  # absolute 2.2s / 3.0s

#: Sampled inside the head pad -- before the beep, nothing has fired and
#: nothing should be drawn anywhere.
#:
#: Late in the pad rather than early in it, and that matters. If the head
#: pad stops being threaded into the sprite builder, every state starts a
#: head pad early and Anders' counter reads "1" from 0.5s, half a second
#: before the beep. At 0.3s that mutant is still blank and this check
#: passes; 0.9s catches it. Frame 27 of 30fps, exactly on the boundary,
#: which is why ``_frame`` selects by index.
T_BEFORE_BEEP = 0.9

#: Sampled after Anders' first shot (absolute 1.5s) but before her
#: clock freezes (4.0s) and before Mathias fires at all (2.2s) --
#: deliberately away from both known Task 5 clock-freeze bugs (inclusive
#: ``t == freeze`` double draw), which this test must not paper over or
#: assert around.
T_AFTER_FIRST_SHOT = 2.0

# --- pixel-diff thresholds --------------------------------------------
#
# Measured against this exact fixture (testsrc2 synthetic clip, 640x360
# CANVAS above, ffmpeg 6.1.1) by rendering both files and diffing them
# with the same helpers this test uses. Re-measure if the canvas size,
# theme or fixture clip changes -- these are not derived from first
# principles.
#
# Two independent libx264 crf=20 encodes of the *same* input (no
# overlay, rendered twice) came back bit-identical: 0.0 mean abs diff
# at both sample points and both quadrants. So the non-zero numbers
# below are not encoder noise -- they are the overlay's own video
# chain running an extra ``format=rgba`` / ``overlay`` / back to
# ``format=yuv420p`` round trip through the whole frame even where the
# composited sprite is fully transparent, which shows up as roughly a
# 1-LSB rounding difference:
#   - pre-beep, full canvas minus the strip band: 1.04
#   - post-first-shot, the unreached (row1,col1) quadrant minus the
#     strip band: 0.06
#   - post-first-shot, Anders' own (row0,col0) quadrant, where the
#     counter + clock are actually drawn: 16.79
NOISE_FLOOR_MEAN_ABS_DIFF = 2.0  # measured 0.06-1.04 where nothing is drawn
FIRING_QUADRANT_MIN_MEAN_ABS_DIFF = 5.0  # measured 16.79 with the counter+clock drawn
UNREACHED_QUADRANT_MAX_MEAN_ABS_DIFF = NOISE_FLOOR_MEAN_ABS_DIFF

#: The pre-beep check gets its own ceiling rather than sharing the noise
#: floor, because it guards a different crop: the whole canvas minus the
#: strip band. That crop pools every moving pixel of the source clip and
#: dilutes anything the overlay draws into one corner of one cell, so its
#: numbers are much smaller than the per-element ones below.
#:
#: Every band here was measured on *this* crop (640x360, testsrc2,
#: ffmpeg 6.1.1). An earlier revision of this comment quoted a
#: "16.8-35.1 the overlay actually drawing" band that had been measured
#: on the firing shooter's quadrant and on the sub-boxes further down --
#: not on the box this constant guards:
#:   - same frame, both renders, nothing drawn:              1.04-1.08
#:   - head pad no longer threaded into the sprite builder,
#:     so Anders' counter and split label draw at t=0.9:      4.00
#:   - one frame apart (the old flake, now impossible since
#:     ``_frame`` selects by index):                          3.89-4.82
#:   - a whole quadrant of overlay drawing -- this same crop
#:     sampled at T_AFTER_FIRST_SHOT instead:                 7.06
#: This was 8.0, which sat *above* every one of those: a full quadrant of
#: pre-beep overlay cleared it. 2.5 is the tightest defensible value --
#: ~2.3x over the noise band, and below both the real pre-beep mutant at
#: 4.00 and the 7.06 whole-quadrant draw.
#:
#: It stays a gross gate even so. The instrument that actually holds this
#: line is the counter-corner check further down, whose crop is small
#: enough for a single counter to move it: 1.18 clean against 7.96 under
#: the same head-pad mutation.
PRE_BEEP_MAX_MEAN_ABS_DIFF = 2.5

# --- sprite-vs-clock sub-boxes ------------------------------------------
#
# The whole-quadrant checks above prove *something* from the overlay
# landed in the right cell, but a fully-blanked ``overlay_sprites``
# renderer (counter + split label drawn nowhere at all) still clears
# FIRING_QUADRANT_MIN_MEAN_ABS_DIFF, because the per-tile clock is a
# separate ``drawtext`` chain in ``mp4_grid.py`` that keeps drawing on
# its own. A fully broken sprite panel would ship green under that
# check alone. So the quadrant is additionally split into three coarse,
# non-overlapping quarter/half boxes -- no font-metric precision needed,
# since the sprite panel (``overlay_sprites._draw_panel``: counter at
# the cell's top-left corner, split label bottom-center) and the clock
# (``mp4_grid._clock_filters``: top-right corner) are non-overlapping by
# construction:
#   - COUNTER_BOX: top-left quarter of the cell.
#   - CLOCK_BOX: top-right quarter of the cell.
#   - SPLIT_BOX: bottom half of the cell, inset from both edges.
#
# The counter and the split label are asserted *separately*, never
# pooled into one mean. They are regions of very unequal signal -- the
# split label is a bigger glyph run against a busier part of the frame
# and reads 4x the counter -- so averaging them lets the larger mask the
# smaller: with the counter silently dead the pooled figure is still
# 18.2, comfortably over any threshold the pooled measure could carry,
# while the reverse (split dead) drops to 4.8 and fails. One of the two
# partial failures would ship green. Separate boxes, separate
# thresholds, each measured.
#
# Measured against this fixture (same render as above, T_AFTER_FIRST_SHOT):
#                                       counter   clock   split
#   real render                            8.16   22.62   35.10
#   counter drawing removed from
#   ``overlay_sprites._draw_panel``        1.33   22.71   35.11
#   split-label drawing removed instead    8.16   22.55    1.45
# The clock box is unmoved by either mutation, which is what confirms it
# is drawn by a separate code path (``mp4_grid._clock_filters``) rather
# than by the sprite. Each threshold sits between its own dead and live
# figures: 4.0 is 3x the dead counter and half the live one; 8.0 is 5.5x
# the dead split label and a quarter of the live one.
FIRING_COUNTER_MIN_MEAN_ABS_DIFF = 4.0  # measured 8.16 real, 1.33 with the counter not drawn
FIRING_SPLIT_MIN_MEAN_ABS_DIFF = 8.0  # measured 35.10 real, 1.45 with the split label not drawn
FIRING_CLOCK_MIN_MEAN_ABS_DIFF = 8.0  # measured 22.62 real, unmoved by either sprite mutation

#: One AAC frame, the unit ``nb_read_frames * 1024 / sample_rate``
#: resolves to. The two renders' audio graphs are byte-for-byte
#: identical (overlay only touches the video half -- see
#: ``mp4_grid``'s module docstring), so decoded sample counts should
#: match exactly; this only absorbs a difference of less than one
#: frame that would otherwise be a false failure from an off-by-one in
#: ffprobe's own frame counting.
AAC_FRAME_SECONDS = 1024 / 48000


def _write_audit(path: Path, ms_after_beep: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate(ms_after_beep)
                ]
            }
        ),
        encoding="utf-8",
    )


def _shooter(
    label: str,
    *,
    root: Path,
    trim: Path,
    shots_ms: list[int] | None,
) -> CompareShooterBundle:
    """One shooter with one stage.

    ``shots_ms is None`` is the no-audit shooter: ``audit_path`` names a
    file that is never written, so ``ui.exports.read_audit_data`` hits
    its "missing file" branch and the overlay degrades to no shots for
    that tile -- on the rendered path, not just in ``overlay_data``'s
    own unit tests.
    """
    audit_path = root / label / "audit" / "stage1.json"
    if shots_ms is not None:
        _write_audit(audit_path, shots_ms)
    stage = CompareStageBundle(
        stage_number=1,
        stage_name="Stage 1",
        trim_path=trim,
        audit_path=audit_path,
        beep_offset_in_clip=BEEP_OFFSET_SECONDS,
        duration_seconds=STAGE_DURATION_SECONDS,
        width=1280,
        height=720,
        frame_rate_num=SYNTHETIC_FPS_NUM,
        frame_rate_den=SYNTHETIC_FPS_DEN,
    )
    return CompareShooterBundle(label=label, project_root=root / label, stages_by_number={1: stage})


def _roster(tmp_path: Path, synthetic_source_video: Path) -> list[CompareShooterBundle]:
    """Anders / Bea / Mathias -- alphabetical order fixes the 2x2 slots.

    index0 Anders (row0,col0), index1 Bea (row0,col1), index2 Mathias
    (row1,col0); index3 (row1,col1) is the roster's unreached cell.
    Every tile reads the same synthesized clip; nothing here depends on
    the tiles looking different from one another.
    """
    return [
        _shooter("Anders", root=tmp_path, trim=synthetic_source_video, shots_ms=ANDERS_SHOTS_MS),
        _shooter("Bea", root=tmp_path, trim=synthetic_source_video, shots_ms=None),
        _shooter("Mathias", root=tmp_path, trim=synthetic_source_video, shots_ms=MATHIAS_SHOTS_MS),
    ]


def _render(shooters: list[CompareShooterBundle], tmp_path: Path, *, overlay: bool, name: str) -> Path:
    out = tmp_path / name
    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=out,
        canvas=CANVAS,
        head_pad_seconds=HEAD_PAD_SECONDS,
        tail_pad_seconds=TAIL_PAD_SECONDS,
        overlay=overlay,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / f"work-{name}",
    )
    assert result.failed == (), result.failed
    assert out.exists()
    return out


def _stream_counts(path: Path) -> tuple[int, int]:
    """``(video streams, audio streams)`` per ffprobe."""
    done = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    kinds = done.stdout.split()
    return kinds.count("video"), kinds.count("audio")


def _video_seconds(path: Path) -> float:
    """How long the video stream actually decodes to, not what it claims.

    Read from the timestamp ffmpeg reports as it decodes the last
    frame, the same technique ``test_compare_mp4_grid_render.py`` uses
    -- not ``ffprobe``'s ``duration``, which is exactly the field the
    module docstring above warns is unreliable on these files.
    """
    done = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    stamps = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", done.stderr)
    assert stamps, done.stderr[-2000:]
    hours, minutes, seconds = stamps[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _decoded_audio_seconds(path: Path, stream: str) -> float:
    """``nb_frames * 1024 / sample_rate`` for one audio stream, decoded.

    ``-count_frames`` makes ffprobe actually decode the stream and
    count frames rather than reading the container's own claim, which
    is the whole point: the mov muxer has been proven (see
    ``mp4_grid.SEGMENT_SUFFIX``'s docstring) to shrink boundary AAC
    frame durations while leaving the declared stream ``duration``
    looking fine. 1024 samples/frame is standard AAC; the segments
    that feed the stitch are PCM (no priming, no padding) and the
    single AAC encode happens once, at the stitch, so this is exact
    for the whole file rather than an approximation stacked per
    segment.
    """
    done = subprocess.run(
        [
            FFPROBE, "-v", "error", "-select_streams", stream, "-count_frames",
            "-show_entries", "stream=nb_read_frames,sample_rate",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    values = dict(line.split("=", 1) for line in done.stdout.strip().splitlines() if "=" in line)
    return int(values["nb_read_frames"]) * 1024 / int(values["sample_rate"])


def _frame(path: Path, at: float, tmp_path: Path, tag: str) -> Image.Image:
    """Decode the frame covering ``at`` seconds to a PNG and load it.

    Selected by *frame index*, never by seeking to a timestamp. Both
    sample points this test uses land exactly on a frame boundary
    (``T_BEFORE_BEEP`` 0.9s and ``T_AFTER_FIRST_SHOT`` 2.0s are frames 27
    and 60 of a 30fps canvas, whose pts are ``0.900000`` and
    ``2.000000`` to the tick), so a seek that keeps the
    first frame with ``pts >= target`` is deciding a tie: any sub-tick
    rounding anywhere in the seek path picks the neighbouring frame
    instead. Measured on this fixture, the two renders sampled at the
    same index differ by 1.02-1.04, and one frame apart by 3.89-4.17 --
    so a tie broken differently for the plain and the overlaid file is
    indistinguishable from the overlay drawing something. A ~4% failure
    rate of the pre-beep check at 4.29 is exactly that band.

    ``select=eq(n,N)`` compares integers and cannot tie. The index is
    derived from the pinned canvas rate, which every tile chain applies
    with its own ``fps=`` filter, so it is the rate of the file being
    probed rather than of any source clip.
    """
    png = tmp_path / f"frame-{tag}-{at:g}.png"
    index = round(at * CANVAS.frame_rate_num / CANVAS.frame_rate_den)
    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y", "-v", "error", "-i", str(path),
            "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
            "-frames:v", "1", str(png),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return Image.open(png).convert("RGB")


def _mean_abs_diff(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean absolute per-channel pixel difference over ``box`` (PIL crop coords)."""
    ca = np.asarray(a.crop(box), dtype=np.int16)
    cb = np.asarray(b.crop(box), dtype=np.int16)
    return float(np.abs(ca - cb).mean())


@integration
@needs_ffmpeg
def test_sprite_states_decode_at_the_boundaries_they_were_written_at(tmp_path: Path):
    """The concat demuxer must hand back the boundaries it was given.

    Nothing in the unit tests can see this: the list is a text file, and
    what ffmpeg does with it depends on the sub-demuxer it opens each PNG
    with. Before ``write_concat_list`` pinned the rate, the ``image2``
    demuxer's default 25fps became the stream's time base and every
    boundary snapped to the 1/25s grid -- requested ``0, 1.6, 1.7, 2.4,
    2.5, 3.1`` decoded as ``0, 1.6, 1.72, 2.4, 2.52, 3.12``. 1/30s
    boundaries are not expressible on a 1/25s grid at all, so no amount
    of arithmetic on this side fixes it; only the demuxer's own rate does.
    """
    from splitsmith.compare import overlay_sprites

    geometry = overlay_sprites.SpriteGeometry(canvas_width=320, canvas_height=180, rows=2, cols=2)
    theme = load_theme("splitsmith")
    # Boundaries at 0, 1.6, 1.7, 2.4, 2.5, 3.1 -- three of the five are
    # off the 1/25s grid and were the ones that used to move.
    starts = [0.0, 1.6, 1.7, 2.4, 2.5, 3.1]
    total = 3.6
    states = [
        overlay_sprites.OverlayState(
            start_seconds=start,
            duration_seconds=(starts[i + 1] if i + 1 < len(starts) else total) - start,
            panels=(
                overlay_sprites.TilePanel(
                    label="ann",
                    row=0,
                    col=0,
                    present=True,
                    shots_fired=i,
                    expected_shots=None,
                    last_split=None,
                    rank=1 if i else None,
                    delta_to_leader=0.0 if i else None,
                ),
            ),
        )
        for i, start in enumerate(starts)
    ]
    sequence = overlay_sprites.write_sprite_sequence(
        states, geometry, theme=theme, cache_dir=tmp_path / "sprites"
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))

    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "info", "-f", "concat", "-safe", "0",
            "-i", str(list_path), "-vf", "showinfo", "-f", "null", "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    decoded = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", done.stderr)]
    # One line per state plus the trailing repeat, which sits at the end
    # of the last state.
    assert decoded[: len(starts)] == pytest.approx(
        starts, abs=1e-6
    ), f"concat demuxer moved the state boundaries: requested {starts}, decoded {decoded}"
    assert decoded[len(starts)] == pytest.approx(total, abs=1e-6)


@integration
@needs_ffmpeg
def test_overlay_reaches_the_rendered_pixels(tmp_path: Path, synthetic_source_video: Path):
    """Render the same stage twice, with and without --overlay, and
    compare decoded frames. A command-string assertion cannot tell you
    the viewer sees anything."""
    shooters = _roster(tmp_path, synthetic_source_video)
    plain = _render(shooters, tmp_path, overlay=False, name="plain.mp4")
    overlaid = _render(shooters, tmp_path, overlay=True, name="overlaid.mp4")

    # --- invariant 1: the overlay must not change the stream layout ----
    # 3 shooters + the mix = 4 audio streams, on both renders. This is
    # exactly what lets the stitch ``concat -c copy`` the video: the
    # per-stage segments have to agree on stream layout with or without
    # the overlay composited into the video half.
    assert _stream_counts(plain) == (1, 4)
    assert _stream_counts(overlaid) == (1, 4)

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    strip_h = max(48, CANVAS.height // 20)

    anders_quadrant = (0, 0, cell_w, cell_h)
    # Row 1 (Mathias / the unreached cell) touches the strip band; row 0
    # (Anders / Bea) sits entirely above it, so Anders' quadrant needs
    # no band exclusion.
    assert cell_h < CANVAS.height - strip_h, "fixture assumption: row 0 must clear the strip band"
    unreached_quadrant_no_strip = (cell_w, cell_h, 2 * cell_w, CANVAS.height - strip_h)

    # --- before the beep: nothing drawn anywhere -------------------
    before_plain = _frame(plain, T_BEFORE_BEEP, tmp_path, "plain-pre")
    before_overlaid = _frame(overlaid, T_BEFORE_BEEP, tmp_path, "overlay-pre")
    pre_beep_box = (0, 0, CANVAS.width, CANVAS.height - strip_h)
    pre_beep_diff = _mean_abs_diff(before_plain, before_overlaid, pre_beep_box)
    assert pre_beep_diff <= PRE_BEEP_MAX_MEAN_ABS_DIFF, (
        f"overlay drew something before the beep: mean abs diff {pre_beep_diff:.2f} "
        f"outside the strip band (threshold {PRE_BEEP_MAX_MEAN_ABS_DIFF})"
    )

    # The whole-canvas check above is a gate against gross pre-beep
    # drawing, and it dilutes: one shot counter is a few hundred pixels
    # of a 640x312 crop, which moves the mean by well under a tenth.
    # Anders' counter corner is where a head pad that stopped reaching
    # the sprite builder would put a "1" half a second before the beep,
    # so check that region on its own scale.
    pre_beep_counter_diff = _mean_abs_diff(before_plain, before_overlaid, (0, 0, cell_w // 2, cell_h // 2))
    assert pre_beep_counter_diff <= NOISE_FLOOR_MEAN_ABS_DIFF, (
        f"a shot counter is on screen before the beep: mean abs diff "
        f"{pre_beep_counter_diff:.2f} in the firing shooter's counter corner "
        f"(threshold {NOISE_FLOOR_MEAN_ABS_DIFF})"
    )

    # --- after the first shot: Anders' quadrant changed, the unreached
    # cell did not (outside the strip band) ------------------------
    after_plain = _frame(plain, T_AFTER_FIRST_SHOT, tmp_path, "plain-post")
    after_overlaid = _frame(overlaid, T_AFTER_FIRST_SHOT, tmp_path, "overlay-post")

    firing_diff = _mean_abs_diff(after_plain, after_overlaid, anders_quadrant)
    assert firing_diff >= FIRING_QUADRANT_MIN_MEAN_ABS_DIFF, (
        f"no visible overlay content in the firing shooter's own quadrant: "
        f"mean abs diff {firing_diff:.2f} (threshold {FIRING_QUADRANT_MIN_MEAN_ABS_DIFF})"
    )

    # The whole-quadrant check above cannot tell a broken sprite panel
    # from a broken clock -- either alone still moves the quadrant
    # average. Split it into the sprite's own regions (counter,
    # top-left quarter; split label, bottom half) versus the clock's
    # (top-right quarter) and require each to independently show real
    # content. The two sprite regions get one assertion each rather than
    # one pooled mean: see the comment above FIRING_COUNTER_MIN_MEAN_ABS_DIFF
    # for why pooling lets a dead counter ride in on the split label.
    counter_box = (0, 0, cell_w // 2, cell_h // 2)
    clock_box = (cell_w // 2, 0, cell_w, cell_h // 2)
    split_box = (cell_w // 4, cell_h // 2, 3 * cell_w // 4, cell_h)

    counter_diff = _mean_abs_diff(after_plain, after_overlaid, counter_box)
    assert counter_diff >= FIRING_COUNTER_MIN_MEAN_ABS_DIFF, (
        f"no visible shot counter in the firing shooter's quadrant: mean abs diff "
        f"{counter_diff:.2f} (threshold {FIRING_COUNTER_MIN_MEAN_ABS_DIFF})"
    )

    split_diff = _mean_abs_diff(after_plain, after_overlaid, split_box)
    assert split_diff >= FIRING_SPLIT_MIN_MEAN_ABS_DIFF, (
        f"no visible split label in the firing shooter's quadrant: mean abs diff "
        f"{split_diff:.2f} (threshold {FIRING_SPLIT_MIN_MEAN_ABS_DIFF})"
    )

    clock_diff = _mean_abs_diff(after_plain, after_overlaid, clock_box)
    assert clock_diff >= FIRING_CLOCK_MIN_MEAN_ABS_DIFF, (
        f"no visible clock content in the firing shooter's quadrant: "
        f"mean abs diff {clock_diff:.2f} (threshold {FIRING_CLOCK_MIN_MEAN_ABS_DIFF})"
    )

    unreached_diff = _mean_abs_diff(after_plain, after_overlaid, unreached_quadrant_no_strip)
    assert unreached_diff <= UNREACHED_QUADRANT_MAX_MEAN_ABS_DIFF, (
        f"the overlay drew into an empty cell -- an empty cell is not a shooter: "
        f"mean abs diff {unreached_diff:.2f} outside the strip band "
        f"(threshold {UNREACHED_QUADRANT_MAX_MEAN_ABS_DIFF})"
    )

    # --- invariant: the overlay must not extend or shrink the segment --
    plain_seconds = _video_seconds(plain)
    overlaid_seconds = _video_seconds(overlaid)
    assert abs(plain_seconds - overlaid_seconds) <= FRAME_SECONDS, (
        f"overlay changed the rendered duration: {plain_seconds:.3f}s vs {overlaid_seconds:.3f}s "
        f"(one frame is {FRAME_SECONDS:.4f}s)"
    )

    # --- invariant: decoded audio sample counts match, honouring the
    # edit list -- not ffprobe's declared duration (see module
    # docstring for why that field lied by 351ms on this codebase
    # before) ----------------------------------------------------------
    for slot in range(4):
        plain_audio = _decoded_audio_seconds(plain, f"a:{slot}")
        overlaid_audio = _decoded_audio_seconds(overlaid, f"a:{slot}")
        assert abs(plain_audio - overlaid_audio) <= AAC_FRAME_SECONDS, (
            f"audio track {slot} decoded to a different length with the overlay on: "
            f"{plain_audio:.4f}s vs {overlaid_audio:.4f}s"
        )
