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

The media comes from ``tests/synthetic_media.py``'s session clip, cut by
``shooter_clips`` into one file per shooter whose container duration is
exactly the length the bundle declares. That equality is the point: in
production the declared length is an ffprobe of the trim, so anything
reading "the end of this clip" has no slack to overrun into, and a
fixture that declares 9s while handing over 24s of media cannot express
what happens when it does.

Anders and Bea read the *same* cut, which is load-bearing for the
summary-hold test below: it compares one tile's cell against another's
within a single frame, and that only isolates drawn text because the
picture underneath is identical by construction. Mathias reads a shorter
cut, which is load-bearing for the opposite reason -- his cell is black
while the other two still have picture, so anything that confuses "the
end of the action" with "the end of this tile's footage" shows up in his
cell and nowhere else.

Task 9 adds ``--summary-hold`` and its test is the reason this module
matters more than it looks. A hold whose still never reached the filter
graph renders at exit 0, stitches at exit 0, declares the right length in
its container, freezes at the right instant and stays A/V-locked -- so
the stitch succeeding, the stream layout and the A/V measurement all pass
against it. Two things do not, and they cover different faults:

- **Decoded** duration catches a *missing* still. The muxer's stretch is
  a longer duration on the last coded frame, not extra frames, so a
  frame-accurate measurement comes up short by the final segment's hold
  (16.99s against 19.00s on the two-stage fixture). A duration read off
  the container does not notice.
- **The pixels** catch a still that is there but *wrong*: blank,
  unblurred, the wrong stage's, drawn into the wrong cell, or with a
  clock left on it. Nothing else can.

Verified with the still input removed: that frame reads 55.2 against the
composed summary where a correct render reads 1.3, is unblurred, and
still has a clock on it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle
from splitsmith.overlay_theme import load_theme
from tests.compare_fixture import (
    HEAD_PAD_SECONDS,
    SEGMENT_SECONDS,
    TAIL_PAD_SECONDS,
    build_clips,
    build_roster,
)

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

# --- fixture geometry and scoring ---------------------------------------
#
# Clip lengths, the beep offset, the pads, the shot times and the roster's
# scoring all live in ``tests/compare_fixture.py``, shared with
# ``scripts/render_grid_frames.py`` so the fixture that has to catch a
# defect is also the one a design pass looks at.
#
# The part of it this module depends on most: each shooter is handed a
# real clip of its own, cut to an exact frame count, and the bundle
# carries that clip's *probed* duration -- ``build_clips`` asserts the two
# agree. This module used to declare a 9.0s stage while handing every tile
# the shared 24.0s source clip, and those fifteen seconds of slack were
# the only reason the freeze-frame seek landed inside the media at all:
# the extraction targets a time derived from the clip's declared length,
# and in production that length comes off an ffprobe of the trim itself
# (``project_loader``), so it is exact and the seek has nowhere to overrun
# into. The whole stage summary rendered on pure black under shipped
# defaults and this fixture could not express it.

# Every tile's beep lands at the segment's head pad (1.0s -- see
# ``_stage_overlay_plan``'s comment on ``start_seconds``), so absolute
# segment time is always ``HEAD_PAD_SECONDS + seconds-from-beep``.
# Anders (row0,col0): shots at 1.0+0.5=1.5s and 1.0+3.0=4.0s; her clock
# freezes at head_pad + last_shot = 4.0s. Mathias (row1,col0): shots at
# 1.0+1.2=2.2s and 1.0+2.0=3.0s. T_AFTER_FIRST_SHOT below (2.0s) sits
# after Anders' first shot but before Mathias's -- nobody but Anders has
# any overlay content yet, a clean single-shooter-fired state with no
# confound from another tile changing at the same instant.

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
# Every number below was re-measured after the bottom delta strip was
# removed, against this exact fixture (testsrc2 synthetic clip, 640x360
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
#   - pre-beep, the whole canvas: 1.01
#   - post-first-shot, the unreached (row1,col1) quadrant, whole: 0.04
#   - post-first-shot, Anders' own (row0,col0) quadrant, where the
#     counter + clock are actually drawn: 16.73
#
# The strip is gone, so a state where nobody has fired composites a
# fully transparent sprite and every crop here is the round-trip figure
# alone. That is exactly why the pre-beep gate below can no longer be
# the only instrument: it now passes trivially against a renderer that
# draws nothing ever. The post-shot sub-box checks are what carry that.
NOISE_FLOOR_MEAN_ABS_DIFF = 2.0  # measured 0.04-1.18 where nothing is drawn
FIRING_QUADRANT_MIN_MEAN_ABS_DIFF = 5.0  # measured 16.73 with the counter+clock drawn
UNREACHED_QUADRANT_MAX_MEAN_ABS_DIFF = NOISE_FLOOR_MEAN_ABS_DIFF

#: The pre-beep check gets its own ceiling rather than sharing the noise
#: floor, because it guards a different crop: the whole canvas. That crop
#: pools every moving pixel of the source clip and dilutes anything the
#: overlay draws into one corner of one cell, so its numbers are much
#: smaller than the per-element ones below. It used to exclude a strip
#: band across the bottom; nothing is drawn outside the cells now, so it
#: takes the whole frame.
#:
#: Every band here was re-measured on *this* crop (640x360, testsrc2,
#: ffmpeg 6.1.1):
#:   - same frame, both renders, nothing drawn:              1.01
#:   - head pad no longer threaded into the sprite builder
#:     (``_stage_overlay_plan`` passing ``head_pad_seconds=0.0``),
#:     so Anders' counter and split label draw at t=0.9:      3.55
#:   - a whole quadrant of overlay drawing -- this same crop
#:     sampled at T_AFTER_FIRST_SHOT instead:                 6.20
#: 2.0 sits ~2x over the clean figure and ~1.8x under the real pre-beep
#: mutant. (One frame apart on this crop measures 4.23-4.27, above the
#: mutant -- which is why ``_frame`` selects by index and never seeks:
#: no threshold could separate a mis-tied frame from real drawing.)
#:
#: It stays a gross gate even so. The instrument that actually holds this
#: line is the counter-corner check further down, whose crop is small
#: enough for a single counter to move it: 1.18 clean against 8.00 under
#: the same head-pad mutation.
PRE_BEEP_MAX_MEAN_ABS_DIFF = 2.0

# --- sprite-vs-clock sub-boxes ------------------------------------------
#
# The whole-quadrant checks above prove *something* from the overlay
# landed in the right cell, but a fully-blanked ``overlay_sprites``
# renderer (counter + split label drawn nowhere at all) still clears
# FIRING_QUADRANT_MIN_MEAN_ABS_DIFF -- measured 6.60 against a 5.0
# threshold with ``_draw_panel`` returning immediately -- because the
# per-tile clock is a separate ``drawtext`` chain in ``mp4_grid.py`` that
# keeps drawing on its own. A fully broken sprite panel would ship green
# under that check alone. So the quadrant is additionally split into
# three coarse, non-overlapping quarter/half boxes -- no font-metric
# precision needed, since the sprite panel
# (``overlay_sprites._draw_panel``: counter at the cell's top-left
# corner, split label bottom-center) and the clock
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
# smaller. Separate boxes, separate thresholds, each measured.
#
# Re-measured against this fixture at T_AFTER_FIRST_SHOT, post-removal:
#                                       counter   clock   split  quadrant
#   real render                            8.23   22.61   35.06     16.73
#   counter drawing removed from
#   ``overlay_sprites._draw_panel``        1.37   22.59   35.10     15.01
#   split-label drawing removed instead    8.16   22.56    1.42      8.29
#   the whole panel blanked                1.33   22.65    1.43      6.60
# The clock box is unmoved by any of them, which is what confirms it is
# drawn by a separate code path (``mp4_grid._clock_filters``) rather than
# by the sprite. Each threshold sits between its own dead and live
# figures: 4.0 is ~3x the dead counter and half the live one; 8.0 is
# ~5.6x the dead split label and a quarter of the live one.
FIRING_COUNTER_MIN_MEAN_ABS_DIFF = 4.0  # measured 8.23 real, 1.37 with the counter not drawn
FIRING_SPLIT_MIN_MEAN_ABS_DIFF = 8.0  # measured 35.06 real, 1.42 with the split label not drawn
FIRING_CLOCK_MIN_MEAN_ABS_DIFF = 8.0  # measured 22.61 real, unmoved by any sprite mutation

#: One AAC frame, the unit ``nb_read_frames * 1024 / sample_rate``
#: resolves to. The two renders' audio graphs are byte-for-byte
#: identical (overlay only touches the video half -- see
#: ``mp4_grid``'s module docstring), so decoded sample counts should
#: match exactly; this only absorbs a difference of less than one
#: frame that would otherwise be a false failure from an off-by-one in
#: ffprobe's own frame counting.
AAC_FRAME_SECONDS = 1024 / 48000

# --- the summary hold ---------------------------------------------------
#
# Read ``mp4_grid.GridStagePlan.total_seconds`` before touching any of
# this. The failure these numbers exist to catch does not announce
# itself: a segment whose audio outlasts its video stitches at exit 0
# with no warning, declares the right length in its container, freezes at
# exactly the right instant and stays A/V-locked to +0.1ms -- on the raw
# last action frame, unblurred, with no summary drawn on it. So the
# stitch succeeding, the stream layout and the A/V measurement all pass
# against a completely missing summary.
#
# Two measures below do not, and they are not interchangeable. The
# **decoded** duration catches a missing still, because the muxer stretch
# is a duration on the last coded frame rather than extra frames, so a
# frame-accurate read comes up short by the last segment's hold. Only the
# **pixels** catch a still that is present but wrong -- blank, unblurred,
# the wrong stage's, or with a clock left on it.
HOLD_SECONDS = 2.0

#: Frames of action per segment, then frames of hold. Whole numbers on
#: the pinned 30fps canvas, which is why the canvas is pinned.
ACTION_FRAMES = round(SEGMENT_SECONDS * 30)  # 225
HOLD_FRAMES = round(HOLD_SECONDS * 30)  # 60
SEGMENT_FRAMES = ACTION_FRAMES + HOLD_FRAMES

#: Last frame of the action, first frame of the hold, and one in the
#: middle of the hold (far from either boundary, so nothing here depends
#: on which side of a boundary a tie falls).
LAST_ACTION_INDEX = ACTION_FRAMES - 1
MID_HOLD_INDEX = ACTION_FRAMES + HOLD_FRAMES // 2

#: Late in the action but before any tile's own footage has run out.
#:
#: Not the same frame as :data:`LAST_ACTION_INDEX`, and the difference is
#: the whole blocker. The action runs a tail pad past the longest tile's
#: footage and every tile chain is ``tpad``-ed black across it, so the
#: last *action* frame has no picture in it on any tile -- Anders and Bea
#: end around 6.97s, Mathias at 5.50s, and the action runs to 7.5s. This
#: index (6.833s) is where Anders and Bea still have footage and Mathias
#: does not, which is what lets the two "end of the footage" readings be
#: told apart in pixels.
PICTURE_INDEX = 205
#: The same point in stage 2's hold, which must carry stage 2's figures.
STAGE2_MID_HOLD_INDEX = SEGMENT_FRAMES + MID_HOLD_INDEX

#: A frame inside the hold against the still the render composed for it.
#:
#: This is the instrument. It says the hold shows *exactly* the composed
#: summary -- so the still reached the pixels (a missing one reads 54-55
#: here, being live footage against a blurred still) and nothing was
#: drawn on top of it either (a clock or a sprite surviving into the hold
#: would show up as a local difference). Measured: 1.32 in the hold at
#: both stages, 54.4-55.0 on the action frames either side of it. The
#: residue is the libx264 crf=20 round trip, nothing else.
HOLD_MATCHES_ITS_STILL_MAX = 6.0

#: High-frequency energy (mean |pixel - its own 3x3 box blur|) in the
#: bottom half of Bea's cell -- her label is at the top, so this crop is
#: pure picture with no glyphs in either frame.
#:
#: Measured on this fixture: 1.99 at :data:`PICTURE_INDEX` (live
#: testsrc2, whose colour-bar edges are all the high frequency it has)
#: against 0.20 inside the hold. A 10x drop, and the thresholds sit
#: between: a hold showing raw footage instead of the blurred still reads
#: the action figure.
BLURRED_MAX_HF_ENERGY = 0.8
ACTION_MIN_HF_ENERGY = 1.5

# --- is there a picture in the summary at all --------------------------
#
# The measure that names the blocker. A stage summary whose freeze frames
# were never extracted is text on the canvas's own black background, and
# the text is a few percent of a cell. Measured on this fixture with the
# fix in place: every shooter's cell is 0.0000 pure black at mean luma
# 69-70 inside the hold. The reviewer's failing baseline, a 5-shooter 3x3
# render at shipped CLI defaults, wrote zero freeze PNGs and produced a
# hold frame that was 84.0% pure black at mean luma 10.1.
#
# Per cell rather than over the whole canvas, because the unreached cell
# is legitimately black and pools ~25% of a 2x2 canvas into any
# whole-frame figure -- which is most of the distance between a correct
# render and a broken one.
HOLD_CELL_MAX_BLACK_FRACTION = 0.15
HOLD_CELL_MIN_MEAN_LUMA = 30.0

#: The unreached cell, which is *supposed* to be black, as the control:
#: without it a summary that had gone uniformly grey would satisfy the two
#: thresholds above. Measured 0.98 (the shortfall is encode ringing at the
#: cell edges).
EMPTY_CELL_MIN_BLACK_FRACTION = 0.9

#: A picture crop on the last *action* frame, which is inside the tail pad
#: and therefore black on every tile. Measured 1.000 in Bea's bottom half,
#: which carries no glyphs in any frame. This is what makes "freeze on the
#: end of the action" an unsound target, stated as a measurement rather
#: than as an argument.
TAIL_PAD_MIN_BLACK_FRACTION = 0.95

#: Mathias's cell at :data:`PICTURE_INDEX`, where his shorter clip has
#: already run out while Anders and Bea still have footage. Measured 0.804
#: for him against 0.000 for Anders. The residue below 1.0 is his own shot
#: counter, clock and split label, still drawn over the black.
SHORT_TILE_MIN_BLACK_FRACTION = 0.6

#: Anders' quadrant against Bea's, in the same hold frame.
#:
#: Both cells are the same clip at the same seek with no lead pad, so
#: their blurred freezes are identical by construction and this
#: difference is the summary's ink alone -- no threshold has to absorb a
#: background. Measured 12.01 with Anders' six-line block against Bea's
#: bare label; a hold that drew no text at all reads ~0.
SUMMARY_INK_MIN_MEAN_ABS_DIFF = 5.0

#: Per-cell high-frequency energy inside the hold: sharp glyphs over a
#: blurred field. Measured 5.13 in Anders' cell, 0.86 in Bea's
#: (label only) and 0.00 in the unreached cell. An empty cell is not a
#: shooter and gets no summary, so its ceiling is separate and tight.
TILE_SUMMARY_MIN_HF_ENERGY = 2.0
UNREACHED_CELL_MAX_HF_ENERGY = 0.2

#: Anders' clock corner against Bea's, on the last *action* frame only.
#: Anders has a clock during the action and Bea (no audit, no shots)
#: never has one, so this reads large while the clock is drawn --
#: confirming the corner genuinely carries a clock during the action, so
#: the hold check below (a different measure -- see
#: :data:`HOLD_CLOCK_MAX_MEAN_ABS_DIFF`) is checking something real.
#: Measured 40.91. Self-referential within a single frame, so encode
#: noise cancels; this one is not confounded by Task 6's per-tile detail
#: group because that group only exists in the *hold's* composited
#: still, never during the action.
ACTION_CLOCK_MIN_MEAN_ABS_DIFF = 8.0

#: Anders' clock-corner crop of the in-hold frame against the *same crop
#: of the still* ``write_hold_still`` actually composed for that hold --
#: not against Bea's corner in the same frame.
#:
#: The Anders-vs-Bea version of this check (kept through 682/683, retired
#: here) compared the two shooters' corners within one hold frame and
#: read ~0 on the theory that neither carries anything there once the
#: clock stops. Task 6's three-rail design briefly broke that theory: the
#: corner it read is ``Anchor.TOP_RIGHT``, which during the hold used to
#: legitimately carry Anders' own split-detail group (shot count,
#: Best/Avg/Worst, Draw) -- real content Bea (no audit, no shots) did not
#: get, for the same underlying reason a clock ever differed between
#: them. That made the two shooters' corners stop isolating "is a clock
#: still here" from "does this shooter have a detail group here" -- it
#: read 23.65 on a hold with no clock in it, comfortably over the old 1.0
#: threshold, purely from the detail group's own ink. Task 8's approved
#: bands design (issue #683) deleted the three-rail layout entirely,
#: ``TOP_RIGHT`` included -- the summary draws only at ``TOP_CENTER``
#: (identity) and ``MIDDLE_CENTER`` (the two bands) now, so this specific
#: confound is gone. The comparison against the composed still, below,
#: was kept anyway rather than reverted: it is strictly more precise than
#: an Anders-vs-Bea comparison regardless of what either corner carries,
#: since it checks *this* corner against its own known-correct answer
#: instead of against a different cell's.
#:
#: Comparing against the composed still: the still is the literal PNG
#: :func:`splitsmith.compare.overlay_summary.write_hold_still` wrote to
#: become this hold. Its text is composed through headless Chromium
#: rasterizing CSS (``overlay_html``/``overlay_raster``, issue #683's
#: amendment) -- not PIL, and not ``drawtext`` either; no ``drawtext``
#: filter sits anywhere near this still (see that module's docstring for
#: the actual composition path). Whatever is in the still's corner --
#: nothing, today, since ``TOP_RIGHT`` is unused -- is the *correct*
#: answer for that corner, by construction. So this measures one thing:
#: did the rendered hold show anything the still did not. A ticking
#: clock, a frozen one, or anything else composited on top would all show
#: up here, because none of them are in the still no matter what pixels
#: legitimately belong there.
#:
#: Measured (this fixture, no defect, re-measured after issue #683's F1
#: fit-policy fix): 1.40 on Anders' corner -- lower than
#: :data:`HOLD_MATCHES_ITS_STILL_MAX`'s whole-canvas 1.32-1.36 would
#: suggest as a floor, but still nonzero: this crop is a small, text-free
#: region of the cell today (``TOP_RIGHT`` draws nothing), so it is
#: mostly picture, and even a blurred picture crop carries some of the
#: libx264 round-trip's own residue.
#:
#: Confirmed against the defect this constant is actually named for, not
#: a stand-in: hanging the clock ``drawtext`` chain off the *joined*
#: (post-``concat``, action+hold) stream instead of the action alone, so
#: a frozen clock paints over both stages' holds -- exactly the failure
#: mode ``test_hold_is_concatenated_after_the_action_not_composited_over_it``
#: exists to keep out of the graph, reproduced here on purpose. That read
#: 35.48 on Anders' corner (task-6-report.md's fix-round-2 section has
#: the transcript, from before Task 8's redesign and F1's fit-policy fix)
#: -- a 5.9x margin over the 6.0 threshold, not the ~69x an earlier,
#: harsher-looking but less representative injection (a plain filled
#: box, not a clock) had suggested. That injection was not re-run for
#: this measurement pass; 6.0 still comfortably clears the current clean
#: baseline (1.40) by more than 4x and was not tuned to just clear it.
HOLD_CLOCK_MAX_MEAN_ABS_DIFF = 6.0

#: Bea's own cell, stage 1's hold against stage 2's.
#:
#: The instrument for "the scoring reached the pixels", and the one thing
#: this module could not say at all before #682. Bea has no audit in
#: either stage, so nothing about shots, splits or a clock differs between
#: them; she reads the same clip at the same seek in both, so her frozen
#: picture is identical by construction. The *only* difference is her
#: ``project.json``: stage 1 gives her no scorecard and no stage time, so
#: her cell is her label alone, and stage 2 gives her a scorecard, so it
#: carries the Scoring band -- the six colour-coded hit/fault counts,
#: hit factor and stage time (issue #683 Task 8's approved design draws
#: no placing and no stage percentage at all, on any stage).
#:
#: So this reads large exactly when a scorecard on disk becomes ink on
#: screen. Measured 9.24. A renderer that stopped finding ``project.json``
#: -- which is what every shooter in this fixture used to do, silently --
#: draws the same label in both stages and reads ~0.
SCORECARD_INK_MIN_MEAN_ABS_DIFF = 4.0

#: The unreached cell, same two frames, as the control for the above.
#:
#: Nothing is ever drawn there, so this is what the two hold frames share:
#: if it were not ~0 the frames would differ in their background and the
#: measure above would be reading something other than drawn text.
#: Measured 0.00 -- the two frames are bit-identical in that cell.
STAGE_BACKGROUND_MAX_MEAN_ABS_DIFF = 1.0

#: Stage 1's composed still against stage 2's, over the two cells that
#: carry figures, PNG to PNG with no encode in between. Re-measured after
#: issue #683's Task 8 redesign and F1 fit-policy fix: 9.04 (was 0.687
#: under the earlier three-rail design). No standalone shot count is ever
#: drawn -- issue #683 Task 8's approved bands design has no figure for
#: it -- so this is not "a shot count and a line of split statistics"
#: differing as the number literally used to be justified; it is every
#: figure the two stages' scorecards actually diverge on: the six
#: hit/fault counts, hit factor, stage time, and Best/Avg/Worst/Draw,
#: since stage 2 carries more rounds and different splits throughout.
#: The order-of-magnitude jump from 0.687 reflects the bands design using
#: much more of the cell's width and height than the three-rail one did,
#: not a change in what differs between the stages.
#:
#: A guard on the *fixture*, not on the code. If the two stages ever
#: composed the same summary, the "stage 2's hold carries stage 2's
#: figures" comparison below would be vacuous and would pass against a
#: renderer that put stage 1's still in every segment. Re-measured
#: separation on that comparison: 1.98 against its own still, 10.45
#: against the other stage's.
STILLS_DIFFER_MIN_MEAN_ABS_DIFF = 0.3


@pytest.fixture(scope="module")
def shooter_clips(tmp_path_factory, synthetic_source_video: Path) -> dict[str, tuple[Path, float]]:
    """The roster's clips, each with its probed duration beside it.

    Module-scoped: three tests share the roster and the encode is the
    expensive part. ``tests.compare_fixture.build_clips`` asserts each
    probe against the frame count the clip was cut to, which is what stops
    the fixture drifting back to declaring a length its media has not got.
    """
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("needs a real ffmpeg and ffprobe on PATH")
    root = tmp_path_factory.mktemp("shooter-clips")
    return build_clips(synthetic_source_video, root, ffmpeg=FFMPEG, ffprobe=FFPROBE)


def _roster(
    tmp_path: Path, clips: dict[str, tuple[Path, float]], *, stages: int = 1
) -> list[CompareShooterBundle]:
    """Anders / Bea / Mathias -- alphabetical order fixes the 2x2 slots.

    index0 Anders (row0,col0), index1 Bea (row0,col1), index2 Mathias
    (row1,col0); index3 (row1,col1) is the roster's unreached cell.

    Anders and Bea read the *same* clip and Mathias reads a shorter one,
    and both facts are load-bearing.

    Bea having no audit makes her cell the control for anything about the
    running clock and the blurred picture: she never gets a clock, and she
    reads the same clip at the same seek as Anders, so their freezes are
    pixel-identical by construction and the background subtracts out
    rather than having to be absorbed by a threshold.

    Mathias's clip is shorter, so his tile is black for the last stretch
    of every action while the other two still have picture. Freezing on
    "the last frame of the action" instead of "the last frame of this
    tile's footage" therefore shows up in his cell and nowhere else --
    with three equal clips the two are indistinguishable.

    Each of the three also carries a ``project.json`` with a real
    ``StageScorecard``, which is what puts the Scoring band -- hit
    factor, stage time, and the six colour-coded hit/fault counts -- into
    the rendered summary at all (issue #683 Task 8's approved design
    draws no placing and no stage percentage on either stage). See
    ``tests.compare_fixture.ROSTER`` for the per-stage table: stage 1 is
    the degradations (a DQ, a shooter with neither scorecard nor audit,
    and a manually timed stage), stage 2 is the ranked stage (a tie at
    the top, and raw points ordered differently from stage percentage --
    unused by the rendered summary, but exercised by ``_rank_placings``'
    own tests).
    """
    return build_roster(tmp_path, clips, count=3, stages=stages)


def _render(
    shooters: list[CompareShooterBundle],
    tmp_path: Path,
    *,
    overlay: bool,
    name: str,
    hold: float = 0.0,
) -> Path:
    out = tmp_path / name
    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=out,
        canvas=CANVAS,
        head_pad_seconds=HEAD_PAD_SECONDS,
        tail_pad_seconds=TAIL_PAD_SECONDS,
        overlay=overlay,
        summary_hold_seconds=hold,
        ffmpeg_binary=FFMPEG,
        # A work dir per render: the sprite cache is content-addressed, so
        # a shared one serves PNGs an earlier render made and the renderer
        # under test never runs.
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
    return _frame_at_index(path, round(at * CANVAS.frame_rate_num / CANVAS.frame_rate_den), tmp_path, tag)


def _frame_at_index(path: Path, index: int, tmp_path: Path, tag: str) -> Image.Image:
    """Decode frame number ``index`` to a PNG and load it.

    The index form is the primitive; :func:`_frame` converts a time to
    one. Anything sampling inside a summary hold uses this directly --
    the hold's own boundaries are frame counts (an action of exactly
    ``SEGMENT_SECONDS * fps`` frames followed by ``HOLD_SECONDS * fps``
    more), and converting them back to seconds only to convert them
    forward again would reintroduce the rounding this avoids.
    """
    png = tmp_path / f"frame-{tag}-{index}.png"
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


def _crop_diff(image: Image.Image, left: tuple[int, ...], right: tuple[int, ...]) -> float:
    """Mean absolute difference between two crops of the *same* frame.

    Both crops must be the same size. Used to compare one tile's cell
    against another's when the two share their underlying picture by
    construction: the encode's own noise is then common to both and
    subtracts out, which a cross-file comparison cannot do.
    """
    a = np.asarray(image.crop(left), dtype=np.int16)
    b = np.asarray(image.crop(right), dtype=np.int16)
    assert a.shape == b.shape, (left, right)
    return float(np.abs(a - b).mean())


def _black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Fraction of pixels in ``box`` that are pure black on all channels.

    The measure that names the blocker directly. A summary composed over
    a freeze frame that was never extracted is the canvas's own black
    background with text on it, and text is a few percent of a cell -- so
    this reads near 1.0 there and well under it over any real picture,
    however dark. Mean luma alone does not separate the two nearly as
    cleanly: a dim night-time frame and an empty cell can share a mean.
    """
    pixels = np.asarray(image.crop(box), dtype=np.int16)
    return float((pixels.max(axis=2) == 0).mean())


def _mean_luma(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean luma over ``box``, 0-255."""
    return float(np.asarray(image.crop(box).convert("L"), dtype=np.int16).mean())


def _hf_energy(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean |pixel - its own 3x3 box blur| over ``box``, in luma.

    A blur's whole effect is to remove high spatial frequency, so this
    is the direct measure of "is this crop blurred": a Gaussian-blurred
    still reads near zero, live footage and sharp glyphs read well above
    it. Deliberately not a Laplacian variance -- variance is dominated by
    the few strongest edges, and one surviving glyph could carry a crop
    that is otherwise smooth.
    """
    crop = image.crop(box).convert("L")
    blurred = crop.filter(ImageFilter.BoxBlur(1))
    return float(np.abs(np.asarray(crop, np.int16) - np.asarray(blurred, np.int16)).mean())


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
def test_overlay_reaches_the_rendered_pixels(tmp_path: Path, shooter_clips):
    """Render the same stage twice, with and without --overlay, and
    compare decoded frames. A command-string assertion cannot tell you
    the viewer sees anything."""
    shooters = _roster(tmp_path, shooter_clips)
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

    anders_quadrant = (0, 0, cell_w, cell_h)
    # Nothing is drawn outside the cells any more, so the unreached cell
    # is checked whole -- no band to carve out of it.
    unreached_quadrant = (cell_w, cell_h, 2 * cell_w, CANVAS.height)

    # --- before the beep: nothing drawn anywhere -------------------
    before_plain = _frame(plain, T_BEFORE_BEEP, tmp_path, "plain-pre")
    before_overlaid = _frame(overlaid, T_BEFORE_BEEP, tmp_path, "overlay-pre")
    pre_beep_box = (0, 0, CANVAS.width, CANVAS.height)
    pre_beep_diff = _mean_abs_diff(before_plain, before_overlaid, pre_beep_box)
    assert pre_beep_diff <= PRE_BEEP_MAX_MEAN_ABS_DIFF, (
        f"overlay drew something before the beep: mean abs diff {pre_beep_diff:.2f} "
        f"over the whole canvas (threshold {PRE_BEEP_MAX_MEAN_ABS_DIFF})"
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

    unreached_diff = _mean_abs_diff(after_plain, after_overlaid, unreached_quadrant)
    assert unreached_diff <= UNREACHED_QUADRANT_MAX_MEAN_ABS_DIFF, (
        f"the overlay drew into an empty cell -- an empty cell is not a shooter: "
        f"mean abs diff {unreached_diff:.2f} over the whole cell "
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


@integration
@needs_ffmpeg
def test_an_ffmpeg_without_drawtext_keeps_the_sprites_and_loses_only_the_clock(tmp_path: Path, shooter_clips):
    """The degradation, rendered by a real ffmpeg and read off the pixels.

    The host that reported this has an ffmpeg built without
    ``--enable-libfreetype``; no ffmpeg here is, so the *decision* is
    driven by a probe runner that answers the way that host's binary
    would. Everything after the decision is real: real filter graph, real
    encode, real decoded frames.

    What "degrade, do not fail" has to mean in pixels, against the same
    no-overlay baseline the test above uses:

      - the counter and the split label are still drawn (sprite PNGs
        composited with ``overlay``, which needs no freetype);
      - the clock corner is back at the noise floor (the one thing lost);
      - the stream layout and the duration are untouched, because a
        degraded segment still has to stitch against a normal one.
    """
    from tests.conftest import fake_ffmpeg_probe

    shooters = _roster(tmp_path, shooter_clips)
    plain = _render(shooters, tmp_path, overlay=False, name="plain-nodt.mp4")
    capable = _render(shooters, tmp_path, overlay=True, name="capable-nodt.mp4")

    degraded_path = tmp_path / "degraded.mp4"
    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=degraded_path,
        canvas=CANVAS,
        head_pad_seconds=HEAD_PAD_SECONDS,
        tail_pad_seconds=TAIL_PAD_SECONDS,
        overlay=True,
        ffmpeg_binary=FFMPEG,
        probe_runner=fake_ffmpeg_probe(drawtext=False),
        # A work dir of its own: the sprite cache is content-addressed, so
        # sharing one would serve PNGs another render already made.
        work_dir=tmp_path / "work-degraded",
    )

    assert result.failed == (), result.failed
    assert result.degradation_summary == mp4_grid.OVERLAY_CLOCK_OMITTED_SUMMARY
    assert degraded_path.exists()

    # The stitch refuses segments whose stream layout disagrees, so a
    # degraded stage has to look exactly like a normal one to concat.
    assert _stream_counts(degraded_path) == _stream_counts(capable)
    assert _stream_counts(degraded_path) == (1, 4)

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    counter_box = (0, 0, cell_w // 2, cell_h // 2)
    clock_box = (cell_w // 2, 0, cell_w, cell_h // 2)
    split_box = (cell_w // 4, cell_h // 2, 3 * cell_w // 4, cell_h)

    after_plain = _frame(plain, T_AFTER_FIRST_SHOT, tmp_path, "plain-nodt-post")
    after_capable = _frame(capable, T_AFTER_FIRST_SHOT, tmp_path, "capable-nodt-post")
    after_degraded = _frame(degraded_path, T_AFTER_FIRST_SHOT, tmp_path, "degraded-post")

    # Against the no-overlay baseline: sprites present, clock absent.
    counter_diff = _mean_abs_diff(after_plain, after_degraded, counter_box)
    assert counter_diff >= FIRING_COUNTER_MIN_MEAN_ABS_DIFF, (
        f"the shot counter went missing along with the clock: mean abs diff "
        f"{counter_diff:.2f} (threshold {FIRING_COUNTER_MIN_MEAN_ABS_DIFF})"
    )
    split_diff = _mean_abs_diff(after_plain, after_degraded, split_box)
    assert split_diff >= FIRING_SPLIT_MIN_MEAN_ABS_DIFF, (
        f"the split label went missing along with the clock: mean abs diff "
        f"{split_diff:.2f} (threshold {FIRING_SPLIT_MIN_MEAN_ABS_DIFF})"
    )
    clock_diff = _mean_abs_diff(after_plain, after_degraded, clock_box)
    assert clock_diff <= NOISE_FLOOR_MEAN_ABS_DIFF, (
        f"a clock was drawn on an ffmpeg reported to have no drawtext: mean abs diff "
        f"{clock_diff:.2f} against the no-overlay render (threshold "
        f"{NOISE_FLOOR_MEAN_ABS_DIFF})"
    )

    # And against the capable render: the clock corner is the *only*
    # thing that differs. Without this pair, a degradation that also
    # silently blanked the sprites would pass everything above.
    assert (
        _mean_abs_diff(after_capable, after_degraded, clock_box) >= FIRING_CLOCK_MIN_MEAN_ABS_DIFF
    ), "the capable and degraded renders agree in the clock corner -- neither drew a clock"
    assert (
        _mean_abs_diff(after_capable, after_degraded, counter_box) <= NOISE_FLOOR_MEAN_ABS_DIFF
    ), "dropping the clock moved the shot counter too"

    plain_seconds = _video_seconds(plain)
    degraded_seconds = _video_seconds(degraded_path)
    assert abs(plain_seconds - degraded_seconds) <= FRAME_SECONDS, (
        f"the degraded overlay changed the rendered duration: {plain_seconds:.3f}s vs "
        f"{degraded_seconds:.3f}s (one frame is {FRAME_SECONDS:.4f}s)"
    )


@integration
@needs_ffmpeg
def test_the_summary_hold_reaches_the_rendered_pixels(tmp_path: Path, shooter_clips):
    """Two stages with a summary hold, measured from decoded frames.

    Most of what is cheap passes against a hold with **no still in it**.
    That was measured, not assumed (``GridStagePlan.total_seconds``): a
    segment whose audio outlasts its video stitches at exit 0 with no
    warning, declares the right length in its container, freezes at the
    right instant and stays A/V-locked to +0.1ms -- holding the raw last
    action frame, unblurred, with no summary on it. So the stream layout
    check, the A/V check and the successful stitch below all pass against
    it and none of them is an instrument.

    Two things below are. The duration check is one, because it reads
    **decoded** frames: the muxer's stretch is a duration on the last
    coded frame rather than extra frames, so a missing still shows up as
    16.99s where 19.00s was asked for. But it only catches a still that
    is *absent*. A still that is present and **wrong** -- blank,
    unblurred, the wrong stage's, drawn into the wrong cell, or with a
    clock left on it -- passes the duration check exactly. That is what
    the frame decoded from inside the hold is for, and nothing cheaper
    substitutes for it.

    Two stages rather than one, because a per-stage still that was sliced
    on the wrong stage, or a drift that accumulates per segment, needs a
    second segment to show up in.
    """
    shooters = _roster(tmp_path, shooter_clips, stages=2)
    held = _render(shooters, tmp_path, overlay=True, name="held.mp4", hold=HOLD_SECONDS)
    unheld = _render(shooters, tmp_path, overlay=True, name="unheld.mp4")

    # --- invariant 1: uniform stream layout -----------------------------
    # The stitch is what enforces this and it is the last step, after
    # every stage has been encoded -- so `_render` returning at all is
    # already the real test of it. Restated because a hold that added or
    # dropped a stream is exactly the regression this invariant names.
    assert _stream_counts(held) == (1, 4)
    assert _stream_counts(held) == _stream_counts(unheld)

    # --- the segment is the action plus the hold, twice ------------------
    # From decoded frames: ``_video_seconds`` reads the timestamp ffmpeg
    # reports for the last frame it decoded, which is one frame short of
    # the stream's extent.
    expected = 2 * (SEGMENT_SECONDS + HOLD_SECONDS)
    held_seconds = _video_seconds(held) + FRAME_SECONDS
    assert held_seconds == pytest.approx(expected, abs=FRAME_SECONDS), (
        f"held render is {held_seconds:.3f}s, expected {expected:.3f}s "
        f"(2 x {SEGMENT_SECONDS}s action + 2 x {HOLD_SECONDS}s hold)"
    )
    unheld_seconds = _video_seconds(unheld) + FRAME_SECONDS
    assert unheld_seconds == pytest.approx(2 * SEGMENT_SECONDS, abs=FRAME_SECONDS)

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    anders_cell = (0, 0, cell_w, cell_h)
    bea_cell = (cell_w, 0, 2 * cell_w, cell_h)
    unreached_cell = (cell_w, cell_h, 2 * cell_w, 2 * cell_h)
    # Bea's label sits at the top of her cell, so her bottom half is
    # picture and nothing else in every frame of the render.
    bea_picture = (cell_w, cell_h // 2, 2 * cell_w, cell_h)
    # The clock draws right-aligned in the top corner of its own cell.
    anders_clock = (3 * cell_w // 4, 0, cell_w, cell_h // 4)
    bea_clock = (cell_w + 3 * cell_w // 4, 0, 2 * cell_w, cell_h // 4)
    # Mathias's own clock corner, in his own (bottom-left) cell -- see
    # HOLD_CLOCK_MAX_MEAN_ABS_DIFF's docstring for why both his corner and
    # Anders' need checking: he is the other tile that gets a clock at
    # all, and a defect that only ever composited over one of the two
    # would pass an Anders-only check while a viewer watching Mathias's
    # tile still saw it.
    mathias_clock = (3 * cell_w // 4, cell_h, cell_w, cell_h + cell_h // 4)

    mathias_cell = (0, cell_h, cell_w, 2 * cell_h)

    last_action = _frame_at_index(held, LAST_ACTION_INDEX, tmp_path, "held-action")
    with_picture = _frame_at_index(held, PICTURE_INDEX, tmp_path, "held-picture")
    in_hold = _frame_at_index(held, MID_HOLD_INDEX, tmp_path, "held-hold")

    # --- there is a picture in the summary, and it is each tile's own ----
    #
    # The check the shipped render failed outright: every cell was text on
    # pure black because the freeze seek was derived from the *action's*
    # length and landed past the end of every clip. Asserted as pure-black
    # fraction plus mean luma, per cell, with the deliberately-empty cell
    # as the control.
    for label, box in (("Anders", anders_cell), ("Bea", bea_cell), ("Mathias", mathias_cell)):
        black = _black_fraction(in_hold, box)
        luma = _mean_luma(in_hold, box)
        assert black <= HOLD_CELL_MAX_BLACK_FRACTION, (
            f"{label}'s summary cell is {black:.1%} pure black inside the hold (threshold "
            f"{HOLD_CELL_MAX_BLACK_FRACTION:.0%}) -- the freeze frame never reached it and the "
            "cell is the canvas's own background with text on it"
        )
        assert luma >= HOLD_CELL_MIN_MEAN_LUMA, (
            f"{label}'s summary cell has mean luma {luma:.1f} inside the hold (threshold "
            f"{HOLD_CELL_MIN_MEAN_LUMA}) -- there is no picture under the figures"
        )
    empty_black = _black_fraction(in_hold, unreached_cell)
    assert empty_black >= EMPTY_CELL_MIN_BLACK_FRACTION, (
        f"the unreached cell is only {empty_black:.1%} black inside the hold -- either something "
        "was drawn into a cell that is not a shooter, or the two thresholds above are being "
        f"cleared by a uniformly grey frame (threshold {EMPTY_CELL_MIN_BLACK_FRACTION:.0%})"
    )

    # --- and it cannot have come from the end of the action --------------
    # The action's last frame is inside the tail pad, so it is black on
    # every tile: a freeze taken there is the thing this test caught.
    tail_black = _black_fraction(last_action, bea_picture)
    assert tail_black >= TAIL_PAD_MIN_BLACK_FRACTION, (
        f"the last action frame is only {tail_black:.1%} black in a picture crop -- the fixture "
        "has stopped exercising the tail pad, and 'freeze on the last action frame' would look "
        "correct here while failing in production"
    )
    # Nor from the *longest* tile's footage end: at PICTURE_INDEX Mathias
    # has already run out while Anders still has picture, so one shared
    # seek clamped into range would freeze his cell on black.
    short_black = _black_fraction(with_picture, mathias_cell)
    assert short_black >= SHORT_TILE_MIN_BLACK_FRACTION, (
        f"the short tile is only {short_black:.1%} black at frame {PICTURE_INDEX} -- the fixture "
        "has stopped giving one shooter a shorter clip, so 'each tile's own footage end' is no "
        "longer distinguishable from 'the longest tile's'"
    )
    assert _black_fraction(with_picture, anders_cell) <= HOLD_CELL_MAX_BLACK_FRACTION

    # --- one freeze frame per present tile, per stage --------------------
    # Three present tiles over two stages. Cheap, and it says which layer
    # failed when the pixel checks below go red: no PNGs means the
    # extraction never produced one, and everything after this is a
    # summary drawn on the canvas's own background.
    work_dir = tmp_path / "work-held.mp4"
    for stage in (1, 2):
        frames = sorted(work_dir.glob(f"freeze-stage{stage}-*.png"))
        assert len(frames) == 3, f"stage {stage} wrote {len(frames)} freeze frames, expected 3"
        assert all(path.stat().st_size > 0 for path in frames)

    # --- THE instrument: the hold shows the composed summary and only it -
    still = Image.open(work_dir / "summary-stage1.png").convert("RGB")
    assert still.size == (CANVAS.width, CANVAS.height)
    whole = (0, 0, CANVAS.width, CANVAS.height)
    to_still = _mean_abs_diff(in_hold, still, whole)
    assert to_still <= HOLD_MATCHES_ITS_STILL_MAX, (
        f"the hold is not showing the still this render composed for it: mean abs diff "
        f"{to_still:.2f} over the whole canvas (threshold {HOLD_MATCHES_ITS_STILL_MAX}). "
        f"The same measure against the last action frame is "
        f"{_mean_abs_diff(last_action, still, whole):.2f} -- if this reads near that, the "
        "video half never got the still and the segment is holding raw footage."
    )

    # --- the held frame is blurred ---------------------------------------
    # Against PICTURE_INDEX, not the last action frame: the last action
    # frame is tail-pad black, and "blurrier than black" is not a claim
    # about a blur.
    action_hf = _hf_energy(with_picture, bea_picture)
    hold_hf = _hf_energy(in_hold, bea_picture)
    assert action_hf >= ACTION_MIN_HF_ENERGY, f"the action frame is not sharp: {action_hf:.2f}"
    assert hold_hf <= BLURRED_MAX_HF_ENERGY, (
        f"the held frame is not blurred: high-frequency energy {hold_hf:.2f} against "
        f"{action_hf:.2f} on the last frame with a picture in it (threshold "
        f"{BLURRED_MAX_HF_ENERGY})"
    )

    # --- and it carries the summary's ink, in each tile's own cell -------
    # Anders and Bea read the same clip at the same seek with no lead
    # pad, so their blurred freezes are identical and this difference is
    # drawn text alone. A summary that composed the blur but drew nothing
    # reads ~0 here while passing every check above.
    ink = _crop_diff(in_hold, anders_cell, bea_cell)
    assert ink >= SUMMARY_INK_MIN_MEAN_ABS_DIFF, (
        f"no per-tile summary text in the hold: Anders' cell (a full stat block) differs from "
        f"Bea's (label only, no audit) by {ink:.2f} (threshold {SUMMARY_INK_MIN_MEAN_ABS_DIFF})"
    )
    anders_hf = _hf_energy(in_hold, anders_cell)
    assert (
        anders_hf >= TILE_SUMMARY_MIN_HF_ENERGY
    ), f"nothing sharp in the firing shooter's own cell during the hold: {anders_hf:.2f}"
    unreached_hf = _hf_energy(in_hold, unreached_cell)
    assert unreached_hf <= UNREACHED_CELL_MAX_HF_ENERGY, (
        f"the summary drew into an empty cell -- an empty cell is not a shooter: "
        f"high-frequency energy {unreached_hf:.2f} (threshold {UNREACHED_CELL_MAX_HF_ENERGY})"
    )

    # --- no clock (or anything else) survives into the hold --------------
    # Checked on both tiles that ever get a clock -- Anders and Mathias,
    # not just Anders. A defect that only ever composited a clock over
    # one shooter's hold (see HOLD_CLOCK_MAX_MEAN_ABS_DIFF's docstring)
    # would clear an Anders-only check while still reaching Mathias's.
    for who, clock_box in (("Anders", anders_clock), ("Mathias", mathias_clock)):
        # First: the corner genuinely carries a clock during the action --
        # otherwise the hold check below proves nothing. Bea (no audit, no
        # shots) never gets one, so her corner is the "no clock" reference
        # for both.
        action_clock = _crop_diff(last_action, clock_box, bea_clock)
        assert action_clock >= ACTION_CLOCK_MIN_MEAN_ABS_DIFF, (
            f"no clock during the action on {who}'s tile, so the hold check below proves nothing: "
            f"{action_clock:.2f}"
        )
        # Then: this shooter's clock corner in the hold, against the *same
        # corner of the still that was actually composed for it* -- not
        # against Bea's corner in the same frame. See
        # HOLD_CLOCK_MAX_MEAN_ABS_DIFF's docstring for why the
        # shooter-vs-Bea comparison stopped isolating a clock once Task 6
        # gave that corner legitimate per-tile content. Whatever the
        # still's own corner shows is correct by construction -- it is
        # what `write_hold_still` drew, with no drawtext filter anywhere
        # near it -- so any difference here is something composited over
        # the hold that the still never had, a clock included.
        hold_vs_still_corner = _mean_abs_diff(in_hold, still, clock_box)
        assert hold_vs_still_corner <= HOLD_CLOCK_MAX_MEAN_ABS_DIFF, (
            f"something is on screen over the summary that the composed still does not have: "
            f"{who}'s clock corner in the hold differs from the same corner of the still by "
            f"{hold_vs_still_corner:.2f} (threshold {HOLD_CLOCK_MAX_MEAN_ABS_DIFF}), against "
            f"{action_clock:.2f} for the clock during the action. A frozen clock beside a blurred "
            "summary reads as a stall rather than a conclusion."
        )

    # --- stage 2's hold carries stage 2's figures ------------------------
    # Stage 2 has one more shot than stage 1, so the two stills differ in
    # shot count and split figures. Compared as "closer to its own still
    # than to the other one" rather than against an absolute threshold:
    # a few glyphs are a small fraction of a cell, and a fixed number
    # there would be indistinguishable from the encode's own residue.
    stage2_hold = _frame_at_index(held, STAGE2_MID_HOLD_INDEX, tmp_path, "held-hold2")
    stage2_still = Image.open(work_dir / "summary-stage2.png").convert("RGB")
    # The left column: Anders' and Mathias's cells, the two that carry
    # figures. Bea's cell and the unreached one are identical between the
    # stages by construction, so including them only dilutes the signal.
    scored_cells = (0, 0, cell_w, 2 * cell_h)
    stills_differ = _mean_abs_diff(still, stage2_still, scored_cells)
    assert stills_differ >= STILLS_DIFFER_MIN_MEAN_ABS_DIFF, (
        f"the two stages composed indistinguishable summaries ({stills_differ:.2f}). Either "
        "every stage is being handed the same stage's data (a slice on a fixed stage number "
        "reads 0.00 here), or the fixture stopped giving the two stages different figures -- "
        "in which case the comparison below would be vacuous. Check the code first."
    )
    to_own = _mean_abs_diff(stage2_hold, stage2_still, scored_cells)
    to_stage1 = _mean_abs_diff(stage2_hold, still, scored_cells)
    assert (
        _mean_abs_diff(stage2_hold, stage2_still, whole) <= HOLD_MATCHES_ITS_STILL_MAX
    ), f"stage 2's hold does not match the still stage 2 composed: {to_own:.2f}"
    assert to_stage1 > to_own, (
        f"stage 2's hold is no closer to stage 2's summary ({to_own:.2f}) than to stage 1's "
        f"({to_stage1:.2f}) -- the per-stage slice is not reaching the segment"
    )

    # --- the scoring on disk reaches the rendered summary -----------------
    #
    # The gap #682 was filed for. Every shooter in this fixture used to
    # have no ``project.json`` at all, so ``TileStageData.scorecard`` was
    # ``None`` for all of them and the summary silently omitted every
    # scored figure it can draw -- the hit factor, stage time and the six
    # colour-coded hit/fault counts (issue #683 Task 8's design; no
    # placing or stage percentage has been drawn since) -- none of which
    # had ever appeared in a rendered frame.
    #
    # Bea is the measurement. She has no audit in either stage, so shots,
    # splits and the clock are identical between them, and she reads the
    # same clip at the same seek, so her frozen picture is identical too.
    # Stage 1 gives her no scorecard and no stage time (label only); stage
    # 2 gives her a scorecard. Anything that differs between these two
    # frames in her cell came off ``project.json``.
    background = _mean_abs_diff(in_hold, stage2_hold, unreached_cell)
    assert background <= STAGE_BACKGROUND_MAX_MEAN_ABS_DIFF, (
        f"the two hold frames differ by {background:.2f} in the cell nothing is ever drawn in "
        f"(threshold {STAGE_BACKGROUND_MAX_MEAN_ABS_DIFF}) -- they do not share a background, so "
        "the per-cell comparison below would be measuring something other than drawn text"
    )
    scorecard_ink = _mean_abs_diff(in_hold, stage2_hold, bea_cell)
    assert scorecard_ink >= SCORECARD_INK_MIN_MEAN_ABS_DIFF, (
        f"the no-audit shooter's cell is the same in both holds ({scorecard_ink:.2f}, threshold "
        f"{SCORECARD_INK_MIN_MEAN_ABS_DIFF}). She has a scorecard on stage 2 and none on stage 1, "
        "and nothing else about her differs -- so her stage time, hit factor and hit counts are "
        "not reaching the pixels. Check that project.json is being read at all before touching "
        "this threshold."
    )

    # And every scored cell carries sharp text of its own, not just the
    # one that also has an audit: a summary that drew the shooter with
    # shots and skipped the rest would pass the measure above.
    for label, box in (("Anders", anders_cell), ("Bea", bea_cell), ("Mathias", mathias_cell)):
        cell_hf = _hf_energy(stage2_hold, box)
        assert cell_hf >= TILE_SUMMARY_MIN_HF_ENERGY, (
            f"nothing sharp in {label}'s cell during stage 2's hold: {cell_hf:.2f} (threshold "
            f"{TILE_SUMMARY_MIN_HF_ENERGY}) -- that shooter's scored summary was not drawn"
        )

    # --- audio runs the whole segment, every track equally ---------------
    lengths = [_decoded_audio_seconds(held, f"a:{slot}") for slot in range(4)]
    assert (
        max(lengths) - min(lengths) <= AAC_FRAME_SECONDS
    ), f"the hold extended some audio tracks and not others: {lengths}"
    assert lengths[0] == pytest.approx(expected, abs=FRAME_SECONDS + AAC_FRAME_SECONDS), (
        f"decoded audio is {lengths[0]:.4f}s, expected {expected:.3f}s -- the hold has to carry "
        "silence through every track, not stop where the picture froze"
    )

    # --- two stages, and no offset accumulating across the join ----------
    # ``concat -c copy`` accepts a segment whose streams disagree in
    # length without a word, and the resulting error grows with segment
    # count. So the held render's A/V gap has to be the *same* as the
    # unheld one's, not merely small.
    held_gap = lengths[0] - held_seconds
    unheld_gap = _decoded_audio_seconds(unheld, "a:0") - unheld_seconds
    assert held_gap == pytest.approx(unheld_gap, abs=AAC_FRAME_SECONDS), (
        f"the hold moved audio against picture: A/V gap {held_gap * 1000:.1f}ms with the hold "
        f"against {unheld_gap * 1000:.1f}ms without it, over two segments"
    )
