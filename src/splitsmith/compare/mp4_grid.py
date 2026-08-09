"""Direct-to-MP4 renderer for multi-shooter compare grids.

Sits beside :mod:`splitsmith.compare.emitter` (which emits FCPXML) and
consumes the same ``project_loader`` bundles and ``layout`` grid math.
Renders one ffmpeg call per stage -- scale + pad each tile to a uniform
cell, ``xstack`` them into the grid, map a mix of every shooter as
track 1 and each shooter's own audio as tracks 2..N+1 -- then stitches
the per-stage temps with the ``concat`` demuxer, copying the video and
encoding the audio exactly once (see :data:`SEGMENT_SUFFIX`).

Phase 1 adds an opt-in splits overlay: canvas-sized RGBA sprite PNGs
stepped on shot events (see :mod:`splitsmith.compare.overlay_sprites`)
plus a ``drawtext`` clock per tile. It is composited *after* ``xstack``
and touches neither the tile chains nor the audio half of the graph, so
a render with ``overlay=False`` is byte-for-byte the phase 0 render.
Transitions and title cards are still out of scope.

Determinism / testability: command construction is split into pure
functions (:func:`build_stage_command` / :func:`build_concat_command`)
with an injectable runner, mirroring :mod:`splitsmith.mp4_render` and
:mod:`splitsmith.trim`.
"""

from __future__ import annotations

import logging
import math
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..overlay_layout import Anchor, CellScale, anchor_ffmpeg_expr
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..overlay_text import FALLBACK_BUNDLED_FONT, overlay_font_file
from ..overlay_theme import ThemeName, load_theme
from ..runtime import FFmpegCapabilities, ffmpeg_capabilities, quote_filter_value, runtime
from .layout import Layout2Up, choose_grid, grid_shape
from .overlay_data import TileStageData, load_overlay_data
from .overlay_live import write_absent_sprite_sequence, write_sprite_sequence
from .overlay_sprites import (
    SpriteGeometry,
    TilePlacement,
    build_overlay_states,
    theme_font_face,
    write_concat_list,
)
from .project_loader import CompareShooterBundle

logger = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]

#: Called once, before any encoding, with each degradation's ``detail``.
#:
#: The engine decides what to do about a feature-poor ffmpeg; a notice
#: hook is how a CLI or a UI gets to *say* it at the point the decision
#: was made rather than 40 minutes later. It is not the ``runner`` hook:
#: both existing callers count ``runner`` invocations to report "stage N
#: of M", so putting probe or notice traffic through it would misreport
#: every stage.
NoticeHook = Callable[[str], None]

DEFAULT_CANVAS_WIDTH = 3840
DEFAULT_CANVAS_HEIGHT = 2160

#: Last resort only. The render's frame rate follows the audio-source
#: shooter's footage (see :func:`derive_frame_rate`); this is what a
#: canvas reports when nobody pinned a rate and there is no bundle to
#: derive one from.
FALLBACK_FRAME_RATE_NUM = 30000
FALLBACK_FRAME_RATE_DEN = 1001

#: Container for the per-stage temps, and it is not ``.mp4`` on purpose.
#:
#: The segments used to carry AAC and be joined at ``-c copy``. AAC cannot
#: encode a clip of arbitrary length exactly: every encode contributes
#: priming samples at the front and padding at the back, and an MP4 edit
#: list hides them by declaring the true extent. Edit lists do not compose
#: under the concat demuxer, so each segment's priming and padding arrived
#: at the muxer as real decodable samples the timeline had no room for --
#: about 30ms per segment, all of it pushing audio later against picture.
#: A 12-stage match came out 386ms out by its last stage, which is every
#: beep and every shot audibly behind the recoil.
#:
#: Nothing about that is visible in the finished file's metadata. Handed
#: overlapping timestamps, the mov muxer shrinks the two AAC frames either
#: side of each boundary to durations of 1 and 191 samples rather than
#: 1024, so the container declares a timeline 21ms out while the samples
#: are 352ms out. Re-encoding the audio at the stitch does not fix it
#: either: the concat demuxer has already decoded the priming into the
#: stream, so that re-encodes the drift instead of removing it (measured
#: on ffmpeg 6.1.1: 12 segments went from 352ms to 117ms, still growing
#: with segment count).
#:
#: So AAC stays out of the segments entirely. PCM has no priming and no
#: padding, one segment of it is exactly as long as it claims, and the
#: single AAC encode at the stitch contributes exactly one priming, which
#: the output's own edit list accounts for correctly because there is
#: nothing to compose it against. Residual drift is then zero -- not
#: small, zero. Measured on ffmpeg 6.1.1 with a synchronised marker (a
#: black-to-white picture cut and a full-scale audio transient authored on
#: the same instant), rendered through this module at 2, 6 and 12 stages:
#: every marker's sound landed on exactly the intended sample against
#: exactly the intended frame, on every track.
#:
#: Do not go chasing the ~32ms that ``nb_read_packets * 1024 /
#: sample_rate`` reports on these files. That counts *coded* samples,
#: which include the one encode's 1024 priming samples and its partial
#: flushed final frame; MP4 signals priming in the edit list
#: (``elst`` media_time, which this output carries and every conforming
#: player honours) and the flushed tail sits after the last picture.
#: ffprobe's ``initial_padding`` is not the thing to check either -- it
#: reads 0 for every AAC-in-MP4 stream, including files proven
#: sample-exact, because the mov demuxer does not populate it.
#:
#: MP4 does not officially carry PCM; QuickTime does, keeps the source
#: timebase intact (Matroska rounds timestamps to a millisecond, which a
#: 30000/1001 video stream should not be put through), and still lets the
#: video be ``-c copy``'d into the final MP4. The segments live in a work
#: directory created and deleted per render, so the choice costs nothing
#: but disk: s16le at 48kHz stereo is ~1.5 Mbps per shooter, so ~260MB
#: across a 4-shooter match against a 4GB output.
SEGMENT_SUFFIX = ".mov"

#: Audio codec for the per-stage temps. See :data:`SEGMENT_SUFFIX`.
SEGMENT_AUDIO_CODEC = "pcm_s16le"

#: Audio codec and bitrate for the finished file -- applied once, at the
#: stitch, over the whole match.
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_AUDIO_BITRATE = "192k"

#: ``handler_name`` of the merged track, which is always audio stream 0.
#:
#: Every player that is not an NLE reads track 1 and nothing else --
#: YouTube, browser ``<video>``, every social embed. Shipping only
#: per-shooter tracks meant a shared grid played exactly one shooter and
#: the whole multi-track design was invisible to whoever it was sent to.
#: So a mix of every shooter is always present, always first, and always
#: carries the ``default`` disposition; the named per-shooter tracks
#: follow it in the same alphabetical order they always had.
MIX_TRACK_LABEL = "Mix"

#: ``amix`` is given ``normalize=1``, which scales the sum by 1/inputs.
#:
#: It cannot clip, and because shooters' microphones are uncorrelated the
#: sum of N of them grows as sqrt(N) while the divisor grows as N -- so a
#: fully-covered stage lands 10*log10(N)/2 dB under a single shooter's
#: track (measured: -6.0 dB at N=4) rather than the -12 dB a naive
#: reading suggests.
#:
#: Every tile is mixed in, including the silent ``anullsrc`` a shooter
#: with no trim for the stage contributes. That is deliberate.
#: ``normalize`` divides by the number of *inputs*, not the number of
#: inputs carrying signal, so a stage where half the roster is missing
#: comes out 3 dB quieter than a fully-covered one (measured: -9.0 dB
#: against -6.0 dB at N=4, 2 real). Mixing only the tiles that have
#: footage would even that out, at the cost of the level stepping up and
#: down between stages as the roster's coverage changes -- which is far
#: more noticeable across a match-length video than a level that is
#: consistently conservative. A predictable 3 dB is the better trade.
MIX_NORMALIZE = 1

#: Face the ``drawtext`` clock falls back to when a theme resolves to no
#: real font file. It is bundled, so it exists on every host.
#:
#: The clock does not choose its own face: it draws whatever
#: :func:`splitsmith.compare.overlay_sprites.theme_font_face` resolved
#: for the sprite beside it, materialized to a real path because
#: ``drawtext`` opens ``fontfile=`` itself. Pinning this constant here
#: instead is what let the ``clean`` theme render a system-discovered
#: sprite next to a bundled-mono clock -- two typefaces in one overlay.
OVERLAY_CLOCK_FALLBACK_FONT = FALLBACK_BUNDLED_FONT

#: Above this many seconds, a summary hold is almost certainly a typo.
#:
#: Not a limit -- a caller cutting a highlight reel may genuinely want to
#: sit on the summary, and refusing a legal value because it is unusual is
#: worse than saying so. But a hold is charged *per stage*: 300 instead of
#: 3 adds an hour and a half to a 12-stage match, and the render is a
#: 40-minute job whose cost is only obvious when it finishes. So the
#: threshold exists to be said out loud, once, before the encode starts.
SUMMARY_HOLD_WARN_SECONDS = 30.0


class GridRenderError(RuntimeError):
    """ffmpeg refused to render a grid stage or the final stitch."""


@dataclass(frozen=True)
class OverlayDegradation:
    """One part of the overlay this ffmpeg build cannot render.

    Two spellings on purpose. ``detail`` is the whole story including
    what to do about it, printed once before the encode starts.
    ``summary`` is the clause that goes on the *last* line the run
    prints, because a warning at the top of a 40-minute render is a
    warning nobody reads:

        Wrote grid.mp4 (12/12 stages, running clock omitted: this
        ffmpeg was built without drawtext)
    """

    summary: str
    detail: str


#: Short form of the drawtext degradation, for the final summary line.
OVERLAY_CLOCK_OMITTED_SUMMARY = "running clock omitted: this ffmpeg was built without drawtext"


def _drawtext_degradation(capabilities: FFmpegCapabilities) -> OverlayDegradation:
    """The overlay minus its clock is most of the overlay, so degrade.

    Only the running clock is ``drawtext``. The counters and the last
    splits are pre-rendered PNGs composited with ``overlay``, which every
    ffmpeg has -- so a build without freetype loses one number per tile
    rather than the whole feature.
    """
    return OverlayDegradation(
        summary=OVERLAY_CLOCK_OMITTED_SUMMARY,
        detail=(
            f"{capabilities.binary} (ffmpeg {capabilities.version}) has no usable drawtext "
            "filter, so the overlay's running clock is omitted. The per-tile shot counters "
            "and last splits still render. For the clock, use an ffmpeg built with "
            "--enable-libfreetype, and point both SPLITSMITH_FFMPEG and SPLITSMITH_FFPROBE "
            "at it -- a mismatched pair is its own source of confusing failures."
        ),
    )


def _concat_option_refusal(capabilities: FFmpegCapabilities) -> str:
    """Why ``--overlay`` is refused outright on this ffmpeg.

    The sprite input is a concat-demuxer list carrying an ``option
    framerate`` directive per entry. Without that keyword the demuxer
    takes image2's default 25fps as its time base and every state
    boundary snaps to the 1/25s grid, so the overlay would step on the
    wrong frames -- and the run would die on ``unknown keyword`` at the
    first stage anyway. Refusing here costs nothing and leaves the plain
    grid, which needs none of this, working on the same host.
    """
    return (
        f"--overlay needs the concat demuxer's 'option' keyword, which {capabilities.binary} "
        f"(ffmpeg {capabilities.version}) does not support: without it every overlay state "
        "snaps to a 25fps time base and the counters step on the wrong frames. Re-run "
        "without --overlay for the plain grid, or point both SPLITSMITH_FFMPEG and "
        "SPLITSMITH_FFPROBE at an ffmpeg whose concat demuxer accepts 'option' "
        "(verified on 6.1.1 and 7.0.2)."
    )


@dataclass(frozen=True)
class GridCanvas:
    """Output geometry for the whole render.

    Pinned once and applied to every stage: the stitch stream-copies the
    video, and the concat demuxer rejects segments whose video
    parameters differ.

    The size is a product decision and does not follow the footage: a
    2x2 of 1080p tiles is exactly 4K, so that is the default regardless
    of what came in. The *frame rate* is the opposite -- forcing
    30000/1001 onto 30fps GoPro material resamples every frame for
    nothing and risks judder, and it would leave this exporter
    disagreeing with ``compare/emitter.py``, which takes the FCPXML
    sequence rate from the audio-source shooter's first stage. So the
    rate fields default to ``None``, meaning "derive from the footage";
    :func:`render_grid_mp4` resolves them via :func:`derive_frame_rate`
    before any command is built. Pin both fields to override that; the
    pin is honoured exactly.
    """

    width: int = DEFAULT_CANVAS_WIDTH
    height: int = DEFAULT_CANVAS_HEIGHT
    frame_rate_num: int | None = None
    frame_rate_den: int | None = None

    def __post_init__(self) -> None:
        if (self.frame_rate_num is None) != (self.frame_rate_den is None):
            raise ValueError(
                "GridCanvas frame rate must be given as both or neither: got "
                f"frame_rate_num={self.frame_rate_num!r}, frame_rate_den={self.frame_rate_den!r}"
            )

    @property
    def is_frame_rate_pinned(self) -> bool:
        """True when the caller chose a rate, so derivation must not touch it."""
        return self.frame_rate_num is not None and self.frame_rate_den is not None

    @property
    def frame_rate(self) -> tuple[int, int]:
        """The concrete ``(num, den)``, falling back when nothing is pinned.

        The fallback exists for direct callers of
        :func:`build_stage_command`, which have a plan but no bundles to
        derive from. It is never what a full render uses.
        """
        if self.frame_rate_num is None or self.frame_rate_den is None:
            return FALLBACK_FRAME_RATE_NUM, FALLBACK_FRAME_RATE_DEN
        return self.frame_rate_num, self.frame_rate_den

    @property
    def rate_string(self) -> str:
        """``num/den`` as ffmpeg's ``-r`` and ``fps=`` want it."""
        num, den = self.frame_rate
        return f"{num}/{den}"

    @property
    def fps(self) -> float:
        num, den = self.frame_rate
        return num / den

    def with_frame_rate(self, frame_rate_num: int, frame_rate_den: int) -> GridCanvas:
        """This canvas with its rate pinned. Used to apply a derived rate."""
        return replace(self, frame_rate_num=frame_rate_num, frame_rate_den=frame_rate_den)


@dataclass(frozen=True)
class GridTile:
    """One shooter's cell in one stage.

    ``trim_path=None`` means the shooter has no trim for this stage: the
    cell renders black and contributes a silent audio track. The slot is
    never dropped -- doing so would shuffle the grid between stages and
    change the stream count, which breaks the concat stitch.

    ``seek_seconds`` and ``lead_pad_seconds`` are a pair, and at most one
    of them is non-zero -- both are ``0.0`` when the beep falls exactly
    on the head pad. Together they put a tile's beep at exactly
    ``head_pad_seconds`` on the output timeline::

        lead_pad_seconds + (beep_offset_in_clip - seek_seconds) == head_pad_seconds

    That invariant covers tiles that have a clip. A filler tile
    (``trim_path=None``) has no beep to place and leaves all three fields
    at ``0.0``, so it lands at ``0.0`` rather than at ``head_pad``.

    A clip with enough footage before its beep just seeks later into
    itself. A clip whose beep sits closer to its start than the head pad
    cannot seek to a negative time, so the shortfall has to be
    synthesised instead -- without it that tile's beep lands early and
    the grid is desynced, which is the one thing the grid exists to
    prevent.
    """

    label: str
    trim_path: Path | None
    beep_offset_in_clip: float
    seek_seconds: float
    lead_pad_seconds: float
    """Black video + silence to prepend before the clip, in seconds.

    Non-zero only when ``seek_seconds`` clamped at ``0.0``. Filler tiles
    (``trim_path=None``) are black for the whole stage, so there is no
    clip to shift and this stays ``0.0``.
    """

    source_duration_seconds: float
    """How long ``trim_path`` itself runs, in clip time.

    Straight off the loader's probe
    (``CompareStageBundle.duration_seconds``) and ``0.0`` on a filler
    tile, which has no source.

    The tile chain itself never reads this -- it pads and trims to the
    *stage's* length and does not need to know where one clip's footage
    stops. Two things above it do, and for the same reason: the stage
    runs until the *longest* tile's post-beep span is done plus a tail
    pad, so every tile's window ends past its own footage and every tile
    chain is ``tpad``-ed black from its own end to the end of the action.
    :func:`overlay_summary.extract_freeze_frames` reads it to take each
    tile's freeze from the last frame with a picture in it, which is this
    tile's own, at this time; :func:`tile_footage_end_seconds` reads it to
    place the per-tile early summary, which covers that black with the
    tile's own cell of the stage summary from the same instant.

    Required rather than defaulted because a tile that silently reported
    ``0.0`` would freeze on its first frame instead of its last, which
    looks like footage and is the wrong footage -- and would arm that
    tile's summary from the head of the stage.
    """

    row: int
    col: int


@dataclass(frozen=True)
class GridStagePlan:
    """Everything one ffmpeg invocation needs for one stage.

    Two durations, and confusing them is the expensive mistake. See
    :attr:`duration_seconds`, :attr:`hold_seconds` and
    :attr:`total_seconds`.
    """

    stage_number: int
    stage_name: str
    tiles: tuple[GridTile, ...]
    duration_seconds: float
    """The **action**: head pad + the longest post-beep span + tail pad.

    The footage, the tile chains and ``xstack`` run for exactly this
    long and no longer. That is what the end-of-stage freeze *is* -- the
    picture stops here and the still takes over.
    """

    audio_label: str
    """The ``--audio-from`` shooter. Not "whose track plays": the mix does.

    Every shooter is in the mix and every shooter has a named track, so
    this no longer selects anything in the MP4. What it still does is
    seed :func:`derive_frame_rate` and settle the stage's spelling, and
    on the FCPXML path it is the one tile left unmuted.
    """

    rows: int
    cols: int

    hold_seconds: float = 0.0
    """How long the frozen stage summary is held after the action.

    ``0.0``, the default, is the render this module has always produced:
    :attr:`total_seconds` collapses onto :attr:`duration_seconds` and
    every argument comes out byte-identical to the pre-hold argv.

    Defaulted rather than required because every caller that predates
    Milestone B constructs a plan without it, and the no-flags argv is
    pinned by test: nothing opt-in may move an argument on the path a
    user gets with no flags. The stitch stream-copies video across
    segments and refuses segments whose stream *layout* disagrees --
    count, codec, parameters -- at the last step, after the whole match
    has been encoded. Stream *lengths* within a segment are a different
    and quieter problem; see :attr:`total_seconds`.
    """

    def __post_init__(self) -> None:
        if self.hold_seconds < 0:
            raise ValueError(
                f"hold_seconds must not be negative: got {self.hold_seconds}. A negative hold "
                "puts total_seconds below the action, so the segment's audio would end before "
                "its video. Measured on ffmpeg 6.1.1: the stitch does not refuse that -- it "
                "exits 0 without a warning, the missing audio time collapses at the AAC "
                "re-encode, and every later stage's sound arrives early by the shortfall, "
                "accumulating (3s short per segment measured -3000ms after one segment and "
                "-9000ms after three)."
            )

    @property
    def total_seconds(self) -> float:
        """The whole segment: the action followed by the hold.

        **Every audio stream in the segment runs this long**, carrying
        silence through the hold; the video is the action followed by the
        still. Extending the *tile* chains to this instead would run the
        footage on underneath the summary rather than freezing it --
        which looks almost right in a thumbnail and wrong in motion.

        The hold lives inside the stage's own segment rather than
        becoming a segment of its own so the cross-stage stitch stays a
        dumb ``concat -c copy``: a separate hold segment would have to
        match the stream layout exactly anyway and would double the
        number of segments to keep uniform.

        **What the stitch actually does with a length mismatch, measured
        on ffmpeg 6.1.1 rather than reasoned about** -- it does not
        refuse one, in either direction. It exits 0 and prints no
        warning, and the two directions then behave completely
        differently, because the video is ``-c copy``'d (timestamps
        preserved exactly) while the audio is re-encoded (a gap in the
        samples simply collapses):

        * **Audio longer than video** -- what this hold does. The mov
          muxer holds the segment's last coded frame for the surplus, so
          the picture freezes and every later stage starts that much
          later on *both* halves. Measured on a four-segment stitch whose
          first three segments each ran 3s over: A/V offset ``+0.1ms`` at
          every marker, i.e. no drift, and a 33.0s file from four 6s
          actions and three 3s holds. That is why getting the video half
          wrong is quiet rather than loud: the freeze happens anyway, in
          the right place, with the sound still locked to it -- just on
          the raw last frame with no summary drawn on it. Hence the
          precondition in :func:`build_stage_command`, which refuses to
          build a segment with a hold and no still to put in it.

          The *stretch* is the muxer's, not the encoder's, and that is
          worth knowing when measuring: the surplus is expressed as a
          longer duration on the last coded frame, never as extra coded
          frames. So a decoded frame count comes up short by exactly the
          final segment's hold while the container's declared duration
          reads correct. Measured on a two-stage render with the still
          dropped: 450 coded frames and a last pts of 16.967 where a
          correct render has 570 and 18.967.
        * **Audio shorter than video** -- what a negative hold would do,
          and the reason ``__post_init__`` rejects one. The missing time
          collapses at the re-encode and every later stage's audio
          arrives *early*, accumulating with segment count: ``-3000ms``
          after one 3s-short segment, ``-9000ms`` after three.
        """
        return self.duration_seconds + self.hold_seconds


def build_stage_plans(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    head_pad_seconds: float,
    tail_pad_seconds: float,
    layout_2up: Layout2Up = "horizontal",
    hold_seconds: float = 0.0,
) -> tuple[GridStagePlan, ...]:
    """Plan one grid stage per stage number present on any shooter.

    Slots are alphabetical by label and stable across stages, matching
    ``compare/emitter.py``'s rule: a label always lands in the same cell
    and a missing trim becomes filler rather than reshuffling the grid.
    Stage names follow the emitter too: the audio-source shooter's
    spelling wins, so the FCPXML and MP4 exports of one match cannot
    label the same stage differently.

    ``hold_seconds`` is the end-of-stage summary hold and reaches every
    plan unchanged -- it is a whole-render setting, not a per-stage one,
    so no stage may come out with a different one. It does not touch the
    pads or the action; see :attr:`GridStagePlan.total_seconds`.
    """
    if not shooters:
        raise ValueError("no shooters to render: build_stage_plans needs at least one loaded shooter")

    labels = sorted(s.label for s in shooters)
    # Stricter than emitter.py, which collapses same-named shooters into
    # one tile. Here ``by_label`` would bind both tiles to the last
    # bundle and drop the first shooter's footage without a word, which
    # is the worse failure. ``CompareManifest._labels_unique`` already
    # rejects duplicates on the CLI path, so nothing shipped regresses.
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        raise ValueError(f"duplicate shooter labels: {', '.join(duplicates)}")

    if head_pad_seconds < 0 or tail_pad_seconds < 0:
        raise ValueError(
            "pads must not be negative: got "
            f"head_pad_seconds={head_pad_seconds}, tail_pad_seconds={tail_pad_seconds}"
        )

    # Checked here as well as in ``GridStagePlan.__post_init__`` so the
    # caller is told which argument it passed, not which field it never
    # named. See that guard for what a negative hold would cost.
    if hold_seconds < 0:
        raise ValueError(f"hold_seconds must not be negative: got hold_seconds={hold_seconds}")

    if audio_label not in labels:
        raise ValueError(f"audio_label={audio_label!r} matches no shooter. Labels: {', '.join(labels)}")

    by_label = {s.label: s for s in shooters}
    audio_bundle = by_label[audio_label]
    # A shooter with no stages at all is a filler everywhere: black tile,
    # silent track, and nothing for :func:`derive_frame_rate` to read, so
    # the whole render silently falls back to 30000/1001 instead of
    # following the footage. Missing a single stage is different, and
    # fine -- that stage just renders their cell black.
    if not audio_bundle.stages_by_number:
        raise ValueError(
            f"audio_label={audio_label!r} has no stages with trims; it drives the render's frame "
            "rate and the FCPXML export's unmuted tile, so it cannot be a shooter with no footage"
        )

    rows, cols = grid_shape(choose_grid(len(labels), layout_2up=layout_2up))

    stage_numbers = sorted({n for s in shooters for n in s.stages_by_number})

    plans: list[GridStagePlan] = []
    for stage_number in stage_numbers:
        tiles: list[GridTile] = []
        post_beep_spans: list[float] = []
        stage_name = ""
        for index, label in enumerate(labels):
            bundle = by_label[label].stages_by_number.get(stage_number)
            row, col = divmod(index, cols)
            if bundle is None:
                tiles.append(
                    GridTile(
                        label=label,
                        trim_path=None,
                        beep_offset_in_clip=0.0,
                        seek_seconds=0.0,
                        lead_pad_seconds=0.0,
                        source_duration_seconds=0.0,
                        row=row,
                        col=col,
                    )
                )
                continue
            # Fallback only: the audio-source shooter's spelling wins when
            # they have this stage. Resolved after the loop.
            stage_name = stage_name or bundle.stage_name
            post_beep_spans.append(bundle.duration_seconds - bundle.beep_offset_in_clip)
            tiles.append(
                GridTile(
                    label=label,
                    trim_path=bundle.trim_path,
                    beep_offset_in_clip=bundle.beep_offset_in_clip,
                    seek_seconds=max(0.0, bundle.beep_offset_in_clip - head_pad_seconds),
                    lead_pad_seconds=max(0.0, head_pad_seconds - bundle.beep_offset_in_clip),
                    source_duration_seconds=bundle.duration_seconds,
                    row=row,
                    col=col,
                )
            )

        # Mirrors emitter.py: prefer the audio-source shooter's spelling of
        # the stage, else the alphabetically-first shooter that has it.
        audio_stage = audio_bundle.stages_by_number.get(stage_number)
        if audio_stage is not None:
            stage_name = audio_stage.stage_name

        # The lead pad does not change this. A tile's content ends at
        # ``lead_pad + (clip duration - seek)``, which reduces to
        # ``head_pad + (clip duration - beep)`` whether or not the seek
        # clamped -- the pad fills exactly the gap the clamp opened. So
        # the stage still runs until the longest post-beep span is done.
        duration = head_pad_seconds + max(post_beep_spans, default=0.0) + tail_pad_seconds
        plans.append(
            GridStagePlan(
                stage_number=stage_number,
                stage_name=stage_name or f"Stage {stage_number}",
                tiles=tuple(tiles),
                duration_seconds=duration,
                audio_label=audio_label,
                rows=rows,
                cols=cols,
                hold_seconds=hold_seconds,
            )
        )
    return tuple(plans)


def derive_frame_rate(shooters: Sequence[CompareShooterBundle], *, audio_label: str) -> tuple[int, int]:
    """The rate the whole render conforms to: the audio source's lowest stage.

    Follows ``compare/emitter.py``, which seeds the FCPXML sequence
    format from ``audio_bundle.stages_by_number[min(...)]``, so a
    whole-match export of one match comes out at the same rate either
    way -- it cannot be 30fps as FCPXML and 29.97 as MP4.

    "Lowest stage" means the lowest stage *in the bundles handed in*,
    not the lowest the shooter shot. The UI path filters bundles to the
    user's stage selection before calling this (see
    ``server._filter_bundles_to_stages``), so exporting only stages 5-12
    seeds from stage 5 and can pick a different rate than a whole-match
    FCPXML of the same shooter would. That is deliberate: the rate
    should follow the footage actually being rendered.

    One rate for the render, not one per stage. A match whose shooters
    or stages carry different rates (30 here, 59.94 there -- ordinary
    for GoPro material) still gets a single pinned rate, because
    the stitch's video stream copy refuses segments whose frame rate
    differs; the
    other tiles are conformed to it by the ``fps=`` filter.

    Falls back to :data:`FALLBACK_FRAME_RATE_NUM` / ``_DEN`` when the
    audio source has no stage to read, which
    :func:`build_stage_plans` rejects before a render ever gets here.
    """
    bundle = next((s for s in shooters if s.label == audio_label), None)
    if bundle is None or not bundle.stages_by_number:
        return FALLBACK_FRAME_RATE_NUM, FALLBACK_FRAME_RATE_DEN
    seed = bundle.stages_by_number[min(bundle.stages_by_number)]
    if seed.frame_rate_num <= 0 or seed.frame_rate_den <= 0:
        return FALLBACK_FRAME_RATE_NUM, FALLBACK_FRAME_RATE_DEN
    return seed.frame_rate_num, seed.frame_rate_den


# --- command construction -------------------------------------------------


def _cell_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]:
    """Uniform cell geometry. Integer division keeps the xstack offsets exact."""
    return canvas.width // plan.cols, canvas.height // plan.rows


def _composed_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]:
    """What ``xstack`` actually composes: the floored cells re-multiplied.

    Equal to the canvas whenever it divides by the grid; up to
    ``cols - 1`` / ``rows - 1`` pixels smaller when it does not. Every
    still that meets the composed video - the hold via ``concat``, the
    early per-tile summary via per-cell crops - must be this size, not
    the canvas's (#691).
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    return cell_w * plan.cols, cell_h * plan.rows


def tile_footage_end_seconds(tile: GridTile) -> float:
    """When this tile's own picture stops, in *segment* time.

    Not the end of the action. The stage runs ``head_pad + the longest
    tile's post-beep span + tail_pad`` and every tile chain is
    ``tpad``-ed with black across the remainder, so this is exactly
    where that black starts.

    Both spellings of a tile's front collapse to the same answer. A tile
    that could seek reads ``source - seek`` of picture with no lead pad;
    one that could not seek far enough back reads its whole clip behind
    ``lead_pad`` seconds of synthesised black. Either way the beep lands
    on the head pad and the picture ends a post-beep span later.

    ``0.0`` for a filler tile, which has no source at all -- see
    :attr:`GridTile.source_duration_seconds`. Clamped at zero rather
    than trusted: the duration is an ffprobe reading of the trim and can
    disagree with the seek by a rounding error, and a negative time
    would arm an ``enable`` expression from the first frame.
    """
    if tile.trim_path is None:
        return 0.0
    return max(0.0, tile.lead_pad_seconds + tile.source_duration_seconds - tile.seek_seconds)


def _unreached_cells(plan: GridStagePlan) -> tuple[tuple[int, int], ...]:
    """``(row, col)`` for every cell of the grid no tile occupies.

    The grid is sized by :func:`choose_grid` for the whole roster, so a
    roster of 3 in a ``2x2`` (or 6 in a ``3x3``) leaves cells nobody
    reaches. ``compare/layout.py`` has always modelled these as
    :attr:`GridLayout.empty_slots` and ``compare/emitter.py`` emits a
    black filler asset for each; this is the same concept for the MP4
    path, and the two exporters have to agree on what an unfilled cell
    looks like.

    Handing them to ``xstack`` unfilled is not neutral. Its default
    ``fill=none`` leaves the unused output region as raw frame buffer,
    which decodes as RGB(0,135,0) -- solid bright green, at every
    timestamp (measured on ffmpeg 6.1.1) -- and its extents shrink to
    the tiles actually stacked, so a 6-shooter render came out
    3840x1440 instead of the 4K canvas it was asked for. The ``fill``
    option would paper over the colour but not the extents, and it
    needs ffmpeg >= 5.1, which this repo does not pin; a real black
    input does both on every version.
    """
    occupied = {(tile.row, tile.col) for tile in plan.tiles}
    return tuple(
        (row, col) for row in range(plan.rows) for col in range(plan.cols) if (row, col) not in occupied
    )


@dataclass(frozen=True)
class TileClock:
    """One tile's running clock, in *segment* time.

    ``start_seconds`` is when the clock starts counting -- the grid's
    head pad, since that is where every tile's beep lands. It is not
    zero: the pre-beep pad is not part of anyone's run.

    ``freeze_seconds`` is where the clock stops, i.e. the shooter's last
    shot on the segment timeline (``head_pad + last_shot_time``), and
    ``final_text`` is what it holds from then on. Both are ``None``
    together for a run with no known end, which leaves the clock ticking
    to the end of the stage with nothing held after it. A tile with no
    shot data at all gets no ``TileClock`` -- a clock over a tile with no
    counters implies a timed run that was never measured.
    """

    row: int
    col: int
    start_seconds: float
    freeze_seconds: float | None
    final_text: str | None


@dataclass(frozen=True)
class StageOverlayPlan:
    """Everything the overlay half of one stage's filter graph needs.

    ``sprite_list_path`` is a concat-demuxer list of RGBA PNGs written by
    :func:`splitsmith.compare.overlay_sprites.write_concat_list`; it is
    read as an extra input, always appended after every tile and every
    unreached-cell input so no existing stream index moves.

    ``font_path`` must be a real file that outlives the render --
    ``drawtext`` opens it itself, so a temp file from
    ``importlib.resources.as_file`` will not do. See
    :func:`splitsmith.overlay_text.materialize_font`.

    ``ink`` and ``stroke`` are the clock's fill and outline, defaulting to
    plain white on black for a caller that has no theme to hand.
    :func:`render_grid_mp4` passes the theme's own values so the clock and
    the sprite text beside it are the same colour.
    """

    sprite_list_path: Path
    font_path: Path
    font_size: int
    clocks: tuple[TileClock, ...] = ()
    ink: tuple[int, int, int] = (255, 255, 255)
    stroke: tuple[int, int, int] = (0, 0, 0)


def _ffmpeg_color(rgb: tuple[int, int, int]) -> str:
    """``drawtext`` colour literal. Hex, because it takes named colours
    only from its own table -- the splitsmith theme's ink is
    ``(244, 244, 245)``, which has no name."""
    red, green, blue = rgb
    return f"0x{red:02x}{green:02x}{blue:02x}"


def _clock_text(seconds: float) -> str:
    """Format an elapsed time the way the ticking filter renders it.

    Truncated to hundredths rather than rounded, so the held value can
    never read above the last value the ticking filter drew.

    The truncation runs on integer milliseconds and not on
    ``math.floor(seconds * 100) / 100``: ``2.09 * 100`` is
    ``208.99999999999997`` in binary floating point, which floors to
    ``2.08`` and would show the clock jumping backwards at the freeze.
    """
    hundredths = round(seconds * 1000) // 10
    return f"{hundredths // 100}.{hundredths % 100:02d}"


def _clock_pad(cell_height: int) -> int:
    """Inset from the cell edge, shared with the sprite's own anchors.

    The sprite insets its shot counter by this same number -- since issue
    #693 as ``overlay_html``'s ``.anchor-top-left { top: pad; left: pad }``
    rather than as a PIL draw at ``(x0 + pad, y0 + pad)``, off the same
    :class:`~splitsmith.overlay_layout.CellScale` field either way. The
    clock sits at the opposite top corner of the same cell, so sharing
    the pad is what makes the two line up.
    """
    return CellScale.for_cell(cell_height).pad


def _video_tail(source_label: str, hold_label: str | None) -> list[str]:
    """Close the video half: concatenate the hold, if any, then convert.

    With no hold this is the single ``format=yuv420p`` step this graph has
    always ended on, so a zero-hold render's argv is untouched.

    With a hold, the frozen summary still is a *second segment* joined
    after the action rather than something composited over it. That is
    what makes the live overlay stop at the freeze for free: the sprite
    ``overlay``, every ``drawtext`` clock and every per-tile early summary
    are all upstream of ``source_label``, which ends at the action, so
    nothing that draws on the action can reach a frame of the hold --
    there is no expression to get wrong. That structural bound is the
    only one there is: the ``enable`` cap :func:`_clock_filters` used to
    carry alongside it was deleted in ``9ab2156`` once it was shown to
    restate what the graph already guarantees.

    ``concat`` demands its inputs agree on size, SAR and frame rate (it
    refuses at graph-config time, not silently), which is why the still's
    own chain repeats the ``scale`` / ``setsar=1`` / ``fps=`` treatment
    every tile chain gets. Pixel format is the one parameter it does not
    demand, because the format negotiation converts the still's RGB to
    whatever the ``format=yuv420p`` below settles on.
    """
    if hold_label is None:
        return [f"[{source_label}]format=yuv420p[final]"]
    return [
        f"[{source_label}][{hold_label}]concat=n=2:v=1:a=0[joined]",
        "[joined]format=yuv420p[final]",
    ]


def _clock_filters(
    plan: GridStagePlan,
    canvas: GridCanvas,
    overlay: StageOverlayPlan,
) -> tuple[list[str], str]:
    """The ``drawtext`` chain hanging off ``[ovlgrid]``, and the label it ends on.

    Two filters per clock, made mutually exclusive by their ``enable``
    expressions: one ticking, one holding the final time. That is two
    filters for a whole stage instead of a per-frame text rasterizer, and
    it stops the clock where the shooter stopped rather than running it
    on to the end of the longest tile.

    Every ticking filter carries a ``gte(t,start)`` lower guard, including
    the open-ended one. Without it the filter runs from frame zero and
    ``t - start`` is negative through the head pad, so the clock reads
    ``-1.00`` at t=0 and counts up to zero as the beep approaches --
    an elapsed time for a run that has not started.

    The upper bound is ``lt``, not the inclusive half of a ``between``.
    ``between(t,start,freeze)`` and the hold's ``gte(t,freeze)`` are both
    true at exactly ``freeze``, so a frame landing there draws both
    filters over each other; measured on ffmpeg 6.1.1, that renders two
    superimposed numbers when the two spellings disagree.

    The escaping is not negotiable and was established against ffmpeg
    6.1.1 rather than reasoned about: inside ``text='...'`` the ``:`` and
    ``,`` separators of ``%{eif:...}`` still have to be backslash-escaped
    or the filtergraph parser splits the option on them.
    ``%{eif:...:d:2}`` zero-pads, so 0.05s renders ``0.05`` and not
    ``0.5``.

    **Known, measured, and deliberately left alone:** the hundredths half
    of that expression reads one hundredth *low* on about 4.6% of frames.
    ``t`` arrives as a binary float, so ``mod((t-start)*100,100)`` lands
    just under the integer it should be and ``trunc`` takes the value
    below -- the clock shows 1.42 on a frame that is 1.43 elapsed.
    Simulated over 95,132 frames (4 frame rates x 4 start offsets): 4.59%
    of frames affected, **zero** backward steps, and across 112 freeze
    scenarios the held ``final_text`` never read below the last value the
    ticking filter drew. So the properties a viewer can perceive -- a
    clock that only ever counts up, and a final time that agrees with the
    last ticked one -- all hold.

    It is not fixed because nothing cheap fixes it. An epsilon added
    inside the expression only gets the affected fraction to 2.52% and is
    identical at 1e-7, 1e-6 and 1e-5, i.e. it does not converge: it moves
    which frames are wrong rather than making them right. Getting it
    exactly right means computing hundredths outside ffmpeg, which means
    one filter per hundredth instead of these two -- thousands of
    ``drawtext`` instances per stage. The two-filter design is worth one
    hundredth on a minority of frames; do not "tidy" this expression into
    a third form without re-measuring both numbers above.

    **Two of these windows are open-ended above, and that is correct.**
    The open-ended tick (``gte(t,start)`` for a run whose end is unknown)
    and the static hold (``gte(t,freeze)``) both run to the end of the
    stream they are attached to, with no ``lt``. A summary hold does not
    change that and must not: these filters hang off ``[ovlgrid]``, which
    is the *action*, and the frozen summary is a second segment joined
    after them by ``concat`` (see :func:`_video_tail`). Their ``t`` is
    the action's own timeline and cannot reach a hold frame.

    A ``*lt(t,duration)`` cap was written here first, on the assumption
    that a clock would otherwise tick over the summary, and then removed:
    it changed no pixel of a rendered hold (the in-hold frame came out
    byte-identical with and without it), while costing a behaviour-free
    branch and making the same ``--overlay`` render emit different
    ``enable`` text depending on an unrelated field. What actually stops
    a clock reaching the summary is the graph's shape, and that shape is
    pinned by
    ``test_hold_is_concatenated_after_the_action_not_composited_over_it``
    -- if a rewrite ever composites the still over one continuous stream
    instead of joining it, that test is what fails, and re-bounding these
    windows is part of what such a rewrite would owe.
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    pad = _clock_pad(cell_h)
    font = quote_filter_value(str(overlay.font_path))
    filters: list[str] = []
    for clock in overlay.clocks:
        x_expr, y_expr = anchor_ffmpeg_expr(
            Anchor.TOP_RIGHT,
            col=clock.col,
            row=clock.row,
            cell_w=cell_w,
            cell_h=cell_h,
            pad=pad,
        )
        common = (
            f"fontfile={font}:fontsize={overlay.font_size}:"
            f"fontcolor={_ffmpeg_color(overlay.ink)}:"
            f"borderw={max(2, overlay.font_size // 18)}:"
            f"bordercolor={_ffmpeg_color(overlay.stroke)}:"
            f"x={x_expr}:y={y_expr}"
        )
        start = f"{clock.start_seconds:g}"
        elapsed = (
            f"text='%{{eif\\:trunc(t-{start})\\:d}}." f"%{{eif\\:trunc(mod((t-{start})*100\\,100))\\:d\\:2}}'"
        )
        if clock.freeze_seconds is None:
            # No known end: tick from the beep to the end of the action,
            # hold nothing after it.
            filters.append(f"drawtext={common}:{elapsed}:enable='gte(t\\,{start})'")
            continue
        freeze = f"{clock.freeze_seconds:g}"
        filters.append(f"drawtext={common}:{elapsed}:enable='gte(t\\,{start})*lt(t\\,{freeze})'")
        if clock.final_text is not None:
            held = quote_filter_value(clock.final_text)
            filters.append(f"drawtext={common}:text={held}:enable='gte(t\\,{freeze})'")
    if not filters:
        return [], "ovlgrid"
    return ["[ovlgrid]" + ",".join(filters) + "[ovltext]"], "ovltext"


def _arm_seconds_string(seconds: float) -> str:
    """Render a non-negative ``enable`` arm time, rounding *down*.

    The direction is the whole point. An arm is deliberately biased one
    frame early (see :func:`_early_summary_filters`), and a to-nearest
    format spec spends that bias: ``{6.966666666666667:g}`` is
    ``6.96667``, which is *above* the number it was asked to print, so a
    tile ending on a whole canvas frame arms one frame later than the
    caller computed and shows the black frame the bias existed to
    cover. More significant digits do not help -- any precision has a
    last digit that can round up. Only the rounding direction does.

    So: floor to milliseconds, and assemble the decimal from integers so
    the division cannot reintroduce a rounding step. The emitted string
    is at most 1ms below ``seconds``.

    **Not "never above it", which is false and cheap to disprove.** The
    ``* 1000.0`` is itself a rounded product, so an input a hair under a
    whole millisecond can round *up* to one and hand ``floor`` a
    millisecond it did not have:
    ``_arm_seconds_string(0.11699999999999999)`` is ``"0.117"``. Measured
    over 800k sampled inputs, the worst overshoot is 1.41e-14 s, at
    ``seconds = 131.128``. That is eleven orders of magnitude under a
    frame at any rate here, and it does not reach the comparison this
    exists for: on a boundary-aligned end the emitted decimal parses back
    to the frame's own presentation time bit-for-bit (``float("6.960")``
    is ``174 / 25.0``), so ``gte`` is satisfied by equality rather than
    by a margin.

    1ms is the granularity because it is far under one frame at every
    rate this renders and deep enough that nothing else notices: a frame
    is 41.7ms at 24fps, 40ms at 25, 33.3ms at 30, 20ms at 50, 16.7ms at
    60 and 8.3ms at 120, so even the fastest plausible canvas has eight
    millisecond steps inside a frame. Going deeper buys no accuracy that
    ``t`` in a filter expression can act on and only lengthens the argv a
    human has to read.

    Non-negative only. ``//`` and ``%`` floor toward negative infinity,
    so a negative input would assemble a nonsense sign; the one caller
    clamps at ``0.0`` before it gets here.
    """
    milliseconds = math.floor(seconds * 1000.0)
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


def _early_summary_filters(
    plan: GridStagePlan,
    canvas: GridCanvas,
    source_label: str,
    early_index: int,
) -> tuple[list[str], str]:
    """Paint each present tile's summary cell from its own footage end.

    A tile's chain is ``tpad``-ed with black from where its own clip runs
    out to the end of the action (see :func:`tile_footage_end_seconds`),
    so a shooter who finished first sat on a black cell until the last
    tile was done. This paints that tile's cell of the stage summary
    over the black instead, leaving the end-of-stage hold to take over
    at ``duration_seconds`` with pixel-identical content -- the cut is
    invisible because both come from the same PNG.

    Cropping the composed still is exact rather than approximate.
    ``overlay_html.grid_html`` gives every cell ``overflow: hidden`` and
    builds its content from that label's own ``TileStageData``, so no
    element crosses a cell boundary and no cell depends on another
    shooter. A crop of the still is therefore the same pixels that cell
    will show during the hold.

    **One frame early**, and the direction matters. Arming late by a
    frame shows a black frame, which is the whole defect; arming early
    covers the tile's last footage frame with a blurred, dimmed copy of
    itself, which nothing can see. ``source_duration_seconds`` is an
    ffprobe reading, so disagreeing with the decoded stream by a fraction
    of a frame is the expected case rather than the exceptional one.

    That margin only survives if the *emitted decimal* is never later
    than the computed arm, which is why the time goes through
    :func:`_arm_seconds_string` rather than a format spec. ``{arm:g}``
    used to round to nearest at six significant digits and lost the
    whole frame on any tile whose footage ended on a canvas frame: a
    7.000s end at 30fps computes 6.966666...s and emitted ``6.96667``,
    above frame 209's own presentation time, so the cell armed at 210
    and 209 stayed black -- rendered and confirmed on the
    ``tests/compare_fixture`` roster at 1280x720@30 with a 2s hold
    (ffmpeg 6.1.1). The same render with the floored emission
    (``6.966``) has Anders' and Bea's cells carrying the summary from
    209, with 208 still live picture. Mathias, whose end (5.4985s) was
    never boundary-aligned, arms at 164 either way.

    Filler tiles get nothing: an empty cell is not a shooter, and
    ``build_hold_still`` draws no summary into one either.

    **Cost.** Not free, and not "one more PNG decode". Measured on a
    12-core box, ffmpeg 6.1.1, a 12-tile 4K grid over 10s of action at
    ``-preset medium -crf 20`` with ``testsrc2`` tile sources: the
    filter graph alone goes 6.96s -> 25.84s, and end to end with libx264
    22.19/22.43s -> 33.93/34.77s. That is about **+1.9s of filter work
    per second of 4K action, ~+53% end to end**, reproducible across
    runs. Three things that measurement is often assumed to say and does
    not:

    * It is paid whether or not any cell arms -- 60.6s against 65.2s
      with every ``enable`` forced past the end of the action.
      ``enable`` skips the blend; ffmpeg still decodes, scales, splits,
      crops and framesyncs the still for every frame.
    * The PNG decode is the minority: reading the early input at
      ``-framerate 1`` and dropping ``fps=`` from its chain recovered
      4.7s of the 18.9s added. The bulk is the N chained ``overlay``
      filters on a 4K main frame.
    * It is linear in tile count, not quadratic -- 22.8s / 30.7s /
      42.0s / 65.2s at 1, 3, 6 and 12 tiles. The constant is large
      because :data:`DEFAULT_CANVAS_WIDTH` is 3840 and no CLI flag
      overrides it.

    Caveat on the ratio, not on the absolute: ``testsrc2`` decodes far
    faster than real H.264, so real footage moves the base up and the
    percentage down. The added ~1.9s per second of action does not
    shrink with it.

    Returns the filters and the label the video half now ends on. The
    caller must keep these upstream of :func:`_video_tail`'s ``concat``
    -- that is what keeps every compositing filter on the action, which
    is the structural bound the hold's correctness rests on.
    """
    present = [tile for tile in plan.tiles if tile.trim_path is not None]
    if not present:
        return [], source_label

    cell_w, cell_h = _cell_size(canvas, plan)
    composed_w, composed_h = _composed_size(canvas, plan)
    frame_seconds = 1.0 / canvas.fps
    branches = "".join(f"[still{index}]" for index in range(len(present)))
    # ``split=1`` is legal but reads as a mistake; ``null`` is the same
    # graph with one output. The scale/setsar/fps conform mirrors the
    # hold chain: it is a no-op on a still this module composed and the
    # guard against one it did not.
    fan_out = f"split={len(present)}" if len(present) > 1 else "null"
    filters = [
        f"[{early_index}:v]setpts=PTS-STARTPTS,scale={composed_w}:{composed_h},"
        f"setsar=1,fps={canvas.rate_string},{fan_out}{branches}"
    ]

    label = source_label
    for index, tile in enumerate(present):
        left = tile.col * cell_w
        top = tile.row * cell_h
        arm = max(0.0, tile_footage_end_seconds(tile) - frame_seconds)
        filters.append(f"[still{index}]crop={cell_w}:{cell_h}:{left}:{top}[cell{index}]")
        filters.append(
            f"[{label}][cell{index}]overlay={left}:{top}:format=auto:"
            f"enable='gte(t\\,{_arm_seconds_string(arm)})'[early{index}]"
        )
        label = f"early{index}"
    return filters, label


def build_stage_command(
    plan: GridStagePlan,
    *,
    canvas: GridCanvas,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    overlay: StageOverlayPlan | None = None,
    hold_still_path: Path | None = None,
) -> tuple[str, ...]:
    """Build the ffmpeg invocation rendering one grid stage.

    Stream layout is fixed at one video plus N+1 audio tracks: the mix
    (see :data:`MIX_TRACK_LABEL`) first, then one track per tile in
    alphabetical label order, regardless of which shooters actually
    have a trim for this stage. The concat demuxer rejects segments
    whose stream layout differs, so a missing tile contributes a black
    ``color`` source and a silent ``anullsrc`` track rather than
    nothing at all.

    Cells the roster does not reach (see :func:`_unreached_cells`) get a
    black ``color`` source too, but *video only*: an empty cell is not a
    shooter, and giving it a track would take the audio count away from
    the roster size plus one -- the very thing the paragraph above pins.

    ``overlay`` is opt-in and defaults to ``None``, which produces
    exactly the argv this produced before the overlay existed. When it is
    given, the sprite sequence is read as one extra input **appended
    after every other input** and composited after ``xstack``. Appending
    last is the whole rule: a filler tile already takes two inputs where
    a real tile takes one, so inserting the sprite anywhere earlier would
    shift every index behind it and put a shooter's audio in another
    shooter's track -- silently, and only audible in the finished file.
    Nothing about the audio graph, the ``-map`` arguments or the tile
    chains changes either way.

    ``hold_still_path`` is the frozen stage summary (one PNG at the
    composed grid size (see ``_composed_size``), written by
    :mod:`splitsmith.compare.overlay_summary`) and is
    **required whenever** ``plan.hold_seconds`` is non-zero. A hold with
    no still is refused here rather than built, because almost nothing
    downstream complains about that segment: measured on ffmpeg 6.1.1,
    its audio simply outlasts its video, the stitch exits 0 without a
    warning, the mov muxer holds the last coded frame for the surplus,
    the container declares the length it should, and the freeze lands in
    the right place with the sound still locked to it -- on the raw last
    action frame, unblurred, with no summary on it.

    Two things do catch it, and knowing which is which matters when
    something looks wrong. A **decoded** frame count comes up short by
    the last segment's hold, because the muxer's stretch is a duration on
    the final coded frame rather than extra frames (see
    :attr:`GridStagePlan.total_seconds`) -- so a duration measured by
    decoding, unlike one read off the container, does notice a *missing*
    still. Only the pixels notice a still that is there but **wrong**:
    blank, unblurred, the wrong stage's, or with a clock left on it. This
    precondition is cheaper than either, and runs before the encode.

    The still is one more input appended after the sprite input, for the
    same reason the sprite goes last, and it is video-only: the hold
    extends the picture, while every audio track already runs
    ``plan.total_seconds`` (see :attr:`GridStagePlan.total_seconds`). A
    still handed in against a zero hold is ignored -- there is no room to
    put it, and the no-flags argv must not move.
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    rate = canvas.rate_string

    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    # Filler tiles take two inputs where a real tile takes one, so a
    # tile's slot is not its input index past the first filler.
    video_index: list[int] = []
    audio_index: list[int] = []
    next_index = 0

    for tile in plan.tiles:
        if tile.trim_path is not None:
            # Seek before -i so ffmpeg fast-seeks; the trim's head buffer
            # absorbs any imprecision, same trade-off as trim.py.
            # A lead-padded tile reads that much less from its source: the
            # synthesised pad at the front supplies the remainder, so the
            # tile still totals ``duration_seconds``.
            args += [
                "-ss",
                f"{tile.seek_seconds:g}",
                "-t",
                f"{plan.duration_seconds - tile.lead_pad_seconds:g}",
                "-i",
                str(tile.trim_path),
            ]
            video_index.append(next_index)
            audio_index.append(next_index)
            next_index += 1
        else:
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration_seconds:g}",
                "-i",
                f"color=c=black:s={cell_w}x{cell_h}:r={rate}",
            ]
            video_index.append(next_index)
            next_index += 1
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration_seconds:g}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
            audio_index.append(next_index)
            next_index += 1

    # Video only, and after every tile input so the tiles' own indices
    # are untouched.
    empty_index: list[int] = []
    for _cell in _unreached_cells(plan):
        args += [
            "-f",
            "lavfi",
            "-t",
            f"{plan.duration_seconds:g}",
            "-i",
            f"color=c=black:s={cell_w}x{cell_h}:r={rate}",
        ]
        empty_index.append(next_index)
        next_index += 1

    # Dead last, after the tiles *and* after the unreached cells. See the
    # docstring: anything else renumbers the streams behind it.
    sprite_index: int | None = None
    if overlay is not None:
        args += ["-f", "concat", "-safe", "0", "-i", str(overlay.sprite_list_path)]
        sprite_index = next_index
        next_index += 1

    # After the sprite, for the same reason the sprite comes after the
    # tiles: a filler tile takes two inputs where a real tile takes one
    # and an unreached cell adds another, so the only index that is safe
    # to occupy is the next free one.
    hold_index: int | None = None
    if plan.hold_seconds > 0:
        if hold_still_path is None:
            raise ValueError(
                f"stage {plan.stage_number} has hold_seconds={plan.hold_seconds:g} but no "
                f"hold_still_path. That segment would carry {plan.total_seconds:g}s of audio "
                f"against {plan.duration_seconds:g}s of video, which almost nothing downstream "
                "reports: the stitch exits 0, the container declares the right length, and the "
                "picture freezes in the right place on the raw last action frame with no summary "
                "drawn on it. Pass the still overlay_summary.write_hold_still wrote, or leave "
                "the hold at 0."
            )
        # ``-framerate`` is the image2 demuxer's own rate; without it a
        # looped still arrives at its 25fps default and the chain's
        # ``fps=`` has to resample a still picture to reach the canvas
        # rate ``concat`` insists on.
        args += [
            "-loop",
            "1",
            "-framerate",
            rate,
            "-t",
            f"{plan.hold_seconds:g}",
            "-i",
            str(hold_still_path),
        ]
        hold_index = next_index
        next_index += 1

    # After the hold's own input, for the same reason the hold went after
    # the sprite: the only index safe to occupy is the next free one.
    # The same PNG, opened a second time and read for the *action* -- see
    # ``_early_summary_filters``. A second input rather than a ``split``
    # off the hold's so each ``-t`` states one length, and so the hold
    # chain, whose length is all that stands between this segment and an
    # audio stream outlasting its video, is left exactly as it was. What
    # this input and its chain cost, measured, is in that function's
    # docstring -- it is a great deal more than the extra PNG decode.
    #
    # Gated on the overlay too, not just the hold. A hold with no overlay
    # is a shape ``render_grid_mp4`` refuses outright, so it reaches no
    # pixel test and must not grow behaviour here.
    early_index: int | None = None
    if hold_index is not None and overlay is not None:
        args += [
            "-loop",
            "1",
            "-framerate",
            rate,
            "-t",
            f"{plan.duration_seconds:g}",
            "-i",
            str(hold_still_path),
        ]
        early_index = next_index
        next_index += 1

    args += [
        "-filter_complex",
        _build_filter_graph(
            plan,
            canvas,
            video_index,
            audio_index,
            empty_index,
            overlay=overlay,
            sprite_index=sprite_index,
            hold_index=hold_index,
            early_index=early_index,
        ),
    ]

    # The mix is mapped first so it lands as audio stream 0. Everything
    # that is not an NLE plays that stream and no other.
    args += ["-map", "[final]", "-map", "[amix]"]
    for slot in range(len(plan.tiles)):
        args += ["-map", f"[a{slot}]"]

    # ``audio_label`` no longer decides which track plays -- the mix
    # always does -- but a plan naming a shooter who has no tile is still
    # incoherent, and it is the field the frame-rate derivation and the
    # FCPXML exporter both key off. ``build_stage_plans`` guarantees it;
    # name it if a hand-built plan disagrees.
    if plan.audio_label not in {t.label for t in plan.tiles}:
        raise ValueError(
            f"audio_label={plan.audio_label!r} matches no tile in stage {plan.stage_number}; "
            f"tiles: {', '.join(t.label for t in plan.tiles)}"
        )
    track_labels = audio_track_labels(t.label for t in plan.tiles)
    args += list(_disposition_args(track_labels, 0))
    args += list(_track_naming_args(track_labels))

    args += [
        "-r",
        rate,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        # PCM, not AAC, and no ``+faststart``. See ``SEGMENT_SUFFIX``:
        # a lossy segment's priming and padding survive the stitch as
        # audible samples, and faststart's second pass over an
        # intermediate nobody streams is pure cost.
        "-c:a",
        SEGMENT_AUDIO_CODEC,
        str(output_path),
    ]
    return tuple(args)


def audio_track_labels(shooter_labels: Iterable[str]) -> tuple[str, ...]:
    """The finished file's audio tracks, in container order.

    One place decides that the mix is track 0 and the shooters follow in
    their own order, so the per-stage segments and the stitch cannot
    disagree about it -- they name the tracks in two separate ffmpeg
    invocations and a mismatch would relabel every shooter.
    """
    return (MIX_TRACK_LABEL, *shooter_labels)


def _track_naming_args(labels: Sequence[str]) -> tuple[str, ...]:
    """Name each audio track after its shooter.

    Both spellings are needed. MP4 has no per-track title box, so
    ``-metadata:s:a:N title=`` alone writes nothing the user can see
    (verified against ffmpeg 7.0.2: the tracks come back out as plain
    ``SoundHandler``); ``handler_name`` is what the container stores and
    what a player shows in its audio-track menu. ``title`` is kept
    because it is the portable spelling every other container uses.
    """
    args: list[str] = []
    for slot, label in enumerate(labels):
        args += [f"-metadata:s:a:{slot}", f"title={label}"]
        args += [f"-metadata:s:a:{slot}", f"handler_name={label}"]
    return tuple(args)


def _disposition_args(labels: Sequence[str], default_slot: int) -> tuple[str, ...]:
    """Mark exactly one audio track as the one that plays by default."""
    args: list[str] = []
    for slot in range(len(labels)):
        args += [f"-disposition:a:{slot}", "default" if slot == default_slot else "0"]
    return tuple(args)


def _build_filter_graph(
    plan: GridStagePlan,
    canvas: GridCanvas,
    video_index: list[int],
    audio_index: list[int],
    empty_index: Sequence[int] = (),
    *,
    overlay: StageOverlayPlan | None = None,
    sprite_index: int | None = None,
    hold_index: int | None = None,
    early_index: int | None = None,
) -> str:
    """Scale + pad every tile to a uniform cell, then ``xstack`` the grid.

    ``force_original_aspect_ratio=decrease`` plus ``pad`` letterboxes
    each source into its cell, so mixed aspect ratios and mixed source
    resolutions both land correctly. ``setsar=1`` is required or
    ``xstack`` refuses inputs whose sample aspect ratios disagree.

    ``empty_index`` names the black sources standing in for the cells no
    tile reaches. They run the same chain as a tile so ``xstack`` sees
    one uniform set of inputs, and they are stacked after the tiles, at
    their own cell offsets.

    The **tile** chains run the action (``plan.duration_seconds``) and the
    audio chains run the whole segment (``plan.total_seconds``). With
    ``hold_seconds=0.0`` those are the same number and this graph is the
    pre-hold graph, argument for argument.

    With a hold, the video half reaches ``total_seconds`` the other way:
    the action is joined to a still by ``concat`` (see
    :func:`_video_tail`), so the footage genuinely stops at the freeze
    rather than being extended. ``hold_index`` names the input the still
    was read at; :func:`build_stage_command` refuses a hold without one,
    because a segment whose audio outlasts its video is accepted in
    silence by everything downstream -- see that function for the
    measurement.

    The overlay, when there is one, is composited onto ``[grid]`` --
    **after** the stack, never inside a tile chain. A tile chain's
    ``tpad`` / ``setpts`` / ``scale`` / ``pad`` / ``setsar`` / ``fps`` /
    ``tpad`` / ``trim`` order is what puts every beep on ``head_pad``;
    reordering it once already cost a silently desynced grid, and the
    overlay has no business anywhere near it.

    ``early_index`` names a second read of the hold still, cropped per
    tile and composited onto the action from each tile's own footage end
    (:func:`_early_summary_filters`). It is composited after the clock
    and before the ``concat``, which is the only position that both
    replaces a finished tile's held clock and stays on the action.
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    composed_w, composed_h = _composed_size(canvas, plan)
    rate = canvas.rate_string
    parts: list[str] = []

    for slot, tile in enumerate(plan.tiles):
        # ``tpad`` must come first, before ``setpts``. Measured on ffmpeg
        # 7.0.2: with ``setpts=PTS-STARTPTS`` ahead of it, a 2.5s input
        # asked for 0.5s of head pad came out 2.52s -- the pad is
        # silently swallowed and the tile's beep lands early, which is
        # the desync the pad exists to prevent. Padding at source size
        # costs nothing: the black frames letterbox like any other.
        lead = (
            f"tpad=start_duration={tile.lead_pad_seconds:g}:start_mode=add:color=black,"
            if tile.lead_pad_seconds > 0
            else ""
        )
        # Tail: every tile has to run the full stage, not stop where its
        # footage does. A tile's content is ``head_pad`` plus its own
        # post-beep span, while the stage runs ``head_pad`` + the longest
        # post-beep span + ``tail_pad`` -- so even the longest tile falls
        # exactly one tail pad short, and the segment's video would end
        # before its audio on every filler-free stage. The stitch then
        # carries that gap into every later stage. Padding by a full
        # stage duration is the one bound that always covers the
        # shortfall without measuring each source; ``trim`` cuts the
        # excess back off, and dropped frames are cheap because nothing
        # downstream encodes them.
        parts.append(
            f"[{video_index[slot]}:v]{lead}setpts=PTS-STARTPTS,"
            f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
            f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={rate},"
            f"tpad=stop_duration={plan.duration_seconds:g}:stop_mode=add:color=black,"
            f"trim=0:{plan.duration_seconds:g}[t{slot}]"
        )

    empty_cells = _unreached_cells(plan)
    for index, source in enumerate(empty_index):
        parts.append(
            f"[{source}:v]setpts=PTS-STARTPTS,"
            f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
            f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={rate},"
            f"tpad=stop_duration={plan.duration_seconds:g}:stop_mode=add:color=black,"
            f"trim=0:{plan.duration_seconds:g}[e{index}]"
        )

    stack_inputs = "".join(f"[t{slot}]" for slot in range(len(plan.tiles)))
    stack_inputs += "".join(f"[e{index}]" for index in range(len(empty_index)))
    placements = [(tile.row, tile.col) for tile in plan.tiles]
    placements += list(empty_cells[: len(empty_index)])
    offsets = "|".join(f"{col * cell_w}_{row * cell_h}" for row, col in placements)
    parts.append(f"{stack_inputs}xstack=inputs={len(placements)}:layout={offsets}[grid]")

    # The still, conformed to exactly what ``concat`` compares: size, SAR
    # and frame rate. ``scale`` is a no-op on a still this module wrote
    # (``build_hold_still`` composes at the composed grid size, which is
    # what ``xstack`` emits - see ``_composed_size``) and the guard against
    # one it did not. ``trim`` restates the length the input's ``-t``
    # already set, so the segment's video extent never depends on how
    # ``-loop 1`` and ``concat``'s eof handling interact.
    hold_label: str | None = None
    if hold_index is not None:
        parts.append(
            f"[{hold_index}:v]setpts=PTS-STARTPTS,scale={composed_w}:{composed_h},"
            f"setsar=1,fps={rate},trim=0:{plan.hold_seconds:g}[hold]"
        )
        hold_label = "hold"

    if overlay is None:
        video_label = "grid"
    else:
        if sprite_index is None:
            raise ValueError("an overlay plan needs the input index its sprite sequence was added at")
        # ``stop_mode=clone`` holds the last state's alpha; the default
        # ``add`` would pad with opaque black and paint the grid out at
        # the end. The explicit ``trim`` means the segment's length never
        # depends on ``overlay``'s ``eof_action`` default, which is what
        # the concat stitch's uniform-stream rule ultimately rests on.
        parts.append(
            f"[{sprite_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,"
            f"tpad=stop_duration={plan.duration_seconds:g}:stop_mode=clone,"
            f"trim=0:{plan.duration_seconds:g}[ovl]"
        )
        parts.append("[grid][ovl]overlay=0:0:format=auto[ovlgrid]")
        clock_parts, video_label = _clock_filters(plan, canvas, overlay)
        parts.extend(clock_parts)

    if early_index is not None:
        if overlay is None:
            # Unreachable from ``build_stage_command``, which only takes
            # an ``early_index`` when it has an overlay. Raised rather
            # than trusted for the same reason the sprite check above is:
            # composited onto ``[grid]`` this would silently produce a
            # render nothing has ever looked at, where a finished tile
            # carries a summary and no live overlay ever ran.
            raise ValueError("an early summary needs the overlay plan whose action chain it draws onto")
        early_parts, video_label = _early_summary_filters(plan, canvas, video_label, early_index)
        parts.extend(early_parts)

    parts.extend(_video_tail(video_label, hold_label))

    for slot, tile in enumerate(plan.tiles):
        # ``aresample=async=1`` keeps a track that starts short from
        # drifting; ``apad`` + ``atrim`` guarantee every track is exactly
        # the segment length so the segment's streams end together.
        # That length is ``total_seconds`` and not ``duration_seconds``:
        # the audio runs silent through the end-of-stage hold while the
        # picture is a frozen still. ``apad`` is unbounded on purpose --
        # it pads until something downstream stops it -- so ``atrim`` is
        # the only place the length is stated, and every track states the
        # same one whether it carries a trim or the filler's
        # ``anullsrc``. Extend one and not the rest and nothing complains:
        # measured on ffmpeg 6.1.1, the stitch accepts unequal lengths and
        # exits 0, and the short track's shortfall then collapses at the
        # AAC re-encode so that one shooter's audio runs early from the
        # next stage on -- and further early with every stage after that.
        # A viewer hears one track out of sync and no log says why.
        # ``adelay`` mirrors the video's ``tpad`` so a lead-padded tile's
        # audio stays locked to its picture.
        # ``aformat`` is the audio half of the concat invariant: a mono
        # trim and the stereo ``anullsrc`` filler would otherwise put
        # differently-shaped tracks in the same slot across segments.
        # ``asplit`` last, not earlier: the mix has to be taken from the
        # tile's *finished* track, after the delay, the format conform and
        # the length clamp. Splitting ahead of any of those would feed
        # ``amix`` a stream that is not the one the user can select, and a
        # lead-padded shooter would sit half a second early in the mix
        # while his own track was on time -- the exact desync the grid
        # exists to prevent, audible only in the track everyone hears.
        delay_ms = int(round(tile.lead_pad_seconds * 1000))
        lead = f"adelay={delay_ms}:all=1," if delay_ms > 0 else ""
        parts.append(
            f"[{audio_index[slot]}:a]asetpts=PTS-STARTPTS,{lead}aresample=async=1,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad,atrim=0:{plan.total_seconds:g},asplit=2[a{slot}][m{slot}]"
        )

    # Every tile, including the silent filler a missing shooter
    # contributes -- see :data:`MIX_NORMALIZE` for why the level is left
    # to pay for that. Built here rather than at the stitch so the
    # concat stays a stream copy of video over PCM it never has to
    # understand.
    mix_inputs = "".join(f"[m{slot}]" for slot in range(len(plan.tiles)))
    parts.append(f"{mix_inputs}amix=inputs={len(plan.tiles)}:normalize={MIX_NORMALIZE}[amix]")

    return ";".join(parts)


def build_concat_command(
    *,
    list_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    audio_labels: Sequence[str] = (),
) -> tuple[str, ...]:
    """Stitch the per-stage temps, copying video and encoding audio once.

    The video is stream-copied: every segment was rendered to the same
    pinned canvas and rate precisely so it can be. The audio cannot be,
    because the segments carry PCM (see :data:`SEGMENT_SUFFIX`) and the
    deliverable is an MP4. That asymmetry is the fix, not a compromise:
    one encode over the whole match contributes one priming instead of
    one per stage, so nothing accumulates across the join.

    ``-map 0`` is load-bearing, not decoration. Without it ffmpeg's
    default stream selection keeps one stream per type, so a stitch of
    four-shooter segments comes out with a single audio track and the
    per-shooter audio the whole feature exists for is gone -- silently,
    at the very last step, after every stage has been encoded (verified
    against ffmpeg 7.0.2).

    ``audio_labels`` is the *shooters*, in slot order; the mix is
    prepended here, so the one rule about which track is which lives in
    :func:`audio_track_labels` and the segments and the stitch cannot
    drift apart on it. Passing nothing restates nothing, which is what a
    caller stitching segments it did not build wants.

    Restating is not optional. Stream copy carries neither the track
    names nor the disposition across the concat demuxer: the names come
    back out as plain ``SoundHandler``, so a four-shooter file would
    offer five anonymous tracks with no way to tell whose is whose.
    """
    args: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-map",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        OUTPUT_AUDIO_CODEC,
        "-b:a",
        OUTPUT_AUDIO_BITRATE,
    ]
    if audio_labels:
        track_labels = audio_track_labels(audio_labels)
        args += list(_track_naming_args(track_labels))
        args += list(_disposition_args(track_labels, 0))
    args += ["-movflags", "+faststart", str(output_path)]
    return tuple(args)


# --- render driver --------------------------------------------------------


@dataclass(frozen=True)
class StageOutcome:
    """What happened to one stage of a grid render."""

    stage_number: int
    stage_name: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class GridRenderResult:
    """Result of a whole grid render, including partial failures.

    ``degradations`` is what the render *did not* do: parts of the
    overlay this host's ffmpeg cannot draw, decided before any encoding
    (see :func:`render_grid_mp4`). It is a returned field and not only a
    log line so every caller -- the CLI today, a UI later -- can put it
    in front of the user without re-deriving the decision.
    """

    output_path: Path
    stages: tuple[StageOutcome, ...]
    degradations: tuple[OverlayDegradation, ...] = ()

    @property
    def failed(self) -> tuple[StageOutcome, ...]:
        return tuple(s for s in self.stages if not s.ok)

    @property
    def degradation_summary(self) -> str:
        """The degradations as one clause, or ``""``. For the final line."""
        return ", ".join(d.summary for d in self.degradations)


def _overlay_data_for_stage(
    data: Mapping[tuple[str, int], TileStageData],
    stage_number: int,
) -> dict[str, TileStageData]:
    """Narrow the whole-match overlay data to one stage, keyed by label.

    :func:`splitsmith.compare.overlay_data.load_overlay_data` is keyed by
    ``(label, stage_number)`` and
    :func:`splitsmith.compare.overlay_sprites.build_overlay_states` is
    keyed by label alone. Handing the wrong one over matches no tile, so
    every panel falls back to empty and the whole overlay renders blank
    -- no crash, no warning. The key-type guard on the far side catches
    the tuple, but not a ``str``-keyed mapping sliced on the wrong stage,
    so the slice lives in one named place rather than inline.
    """
    return {label: tile for (label, number), tile in data.items() if number == stage_number}


def _stage_overlay_plan(
    plan: GridStagePlan,
    canvas: GridCanvas,
    data: Mapping[tuple[str, int], TileStageData],
    *,
    theme_name: ThemeName,
    font_path: Path,
    head_pad_seconds: float,
    work: Path,
    rasterizer: Rasterizer | None,
) -> StageOverlayPlan:
    """Render one stage's sprites and describe its clocks.

    ``rasterizer`` is the seam issue #693 put under the sprites: they are
    an HTML document rasterized by headless Chromium rather than a PIL
    draw. ``None`` is the degradation path, decided once up front by
    :func:`render_grid_mp4`'s preflight rather than per stage -- the
    sprite stream is still written, and still an input to the filter
    graph, but every state paints nothing (see
    :func:`splitsmith.compare.overlay_live.write_absent_sprite_sequence`).
    The clocks are unaffected either way: ``drawtext`` is ffmpeg's own and
    owes the browser nothing.
    """
    theme = load_theme(theme_name)
    stage_data = _overlay_data_for_stage(data, plan.stage_number)
    placements = tuple(
        TilePlacement(label=tile.label, row=tile.row, col=tile.col, present=tile.trim_path is not None)
        for tile in plan.tiles
    )
    geometry = SpriteGeometry(
        canvas_width=canvas.width,
        canvas_height=canvas.height,
        rows=plan.rows,
        cols=plan.cols,
    )
    states = build_overlay_states(
        placements,
        stage_data,
        head_pad_seconds=head_pad_seconds,
        duration_seconds=plan.duration_seconds,
    )
    # One cache directory for the whole run, not one per stage: the cache
    # is content-addressed, so stages that share a state share a PNG. That
    # dedup matters roughly five times more since #693 -- a repeat now
    # skips a browser render rather than a PIL draw.
    if rasterizer is None:
        sequence = write_absent_sprite_sequence(states, geometry, cache_dir=work / "sprites")
    else:
        sequence = write_sprite_sequence(
            states,
            geometry,
            theme=theme,
            cache_dir=work / "sprites",
            rasterizer=rasterizer,
        )
    # The canvas rate, not a guess: the list writer quantises every state
    # boundary onto a whole output frame and pins the demuxer's own time
    # base to it, so the sprite steps on the same frame the clock does.
    list_path = write_concat_list(
        sequence,
        work / f"sprites-stage{plan.stage_number}.txt",
        frame_rate=canvas.frame_rate,
    )

    clocks: list[TileClock] = []
    for tile in plan.tiles:
        if tile.trim_path is None:
            continue
        tile_data = stage_data.get(tile.label)
        last = tile_data.last_shot_time if tile_data is not None else None
        if last is None:
            # No shots read for this tile. A clock here would imply a
            # timed run that was never measured.
            continue
        clocks.append(
            TileClock(
                row=tile.row,
                col=tile.col,
                # Every tile's beep is at the head pad, so that is where
                # every clock starts. Threaded from the caller, never
                # assumed to be 1.0.
                start_seconds=head_pad_seconds,
                freeze_seconds=head_pad_seconds + last,
                # Truncated, not rounded, so the held value cannot read
                # above the last value the ticking filter drew.
                final_text=_clock_text(last),
            )
        )

    _cell_w, cell_h = _cell_size(canvas, plan)
    return StageOverlayPlan(
        sprite_list_path=list_path,
        font_path=font_path,
        # Same resolver the sprite uses, so the clock and the shot counter
        # beside it cannot pick up different sizes.
        font_size=CellScale.for_cell(cell_h).live_primary,
        clocks=tuple(clocks),
        ink=theme.ink,
        stroke=theme.stroke,
    )


def _stage_hold_still(
    plan: GridStagePlan,
    canvas: GridCanvas,
    data: Mapping[tuple[str, int], TileStageData],
    *,
    theme_name: ThemeName,
    work: Path,
    ffmpeg_binary: str,
    runner: Runner,
    rasterizer: Rasterizer | None,
) -> Path:
    """Compose this stage's frozen summary still and return its path.

    ``data`` is the whole-match mapping; the slice to one stage happens
    here, through the same :func:`_overlay_data_for_stage` the sprite half
    uses. ``build_hold_still`` refuses a tuple-keyed mapping outright, but
    a mapping sliced on the *wrong* stage is still str-keyed and would
    render one stage's figures over another's picture in silence, so
    there is exactly one place that slice is written.

    ``rasterizer`` is threaded straight through to
    ``overlay_summary.write_hold_still``/``build_hold_still``: ``None``
    means the summary composes with no text (either no rasterizer was
    ever requested, or :func:`render_grid_mp4`'s own preflight already
    degraded and said so once for the whole render), a live one means the
    box-engine summary renders through it, and this function does not
    itself decide which -- see :func:`render_grid_mp4`'s rasterizer
    preflight, which mirrors its drawtext capability preflight.

    Imported inside the function on purpose:
    :mod:`splitsmith.compare.overlay_summary` imports ``GridStagePlan``
    and ``Runner`` from this module, so a module-level import in either
    direction is a cycle.
    """
    from .overlay_summary import write_hold_still

    composed_w, composed_h = _composed_size(canvas, plan)
    return write_hold_still(
        plan,
        _overlay_data_for_stage(data, plan.stage_number),
        # Composed size, not canvas size: ``SpriteGeometry`` floor-divides
        # its width and height back into cells, and a composed size divides
        # exactly, so the cells come out identical to ``_cell_size``'s and
        # the PNG matches the ``xstack`` output by construction (#691).
        SpriteGeometry(
            canvas_width=composed_w,
            canvas_height=composed_h,
            rows=plan.rows,
            cols=plan.cols,
        ),
        theme=load_theme(theme_name),
        work_dir=work,
        ffmpeg_binary=ffmpeg_binary,
        runner=runner,
        rasterizer=rasterizer,
    )


def _run_ffmpeg(cmd: tuple[str, ...], *, runner: Runner) -> subprocess.CompletedProcess:
    """Invoke ffmpeg, turning a missing binary into a clear error.

    A binary that isn't there is not a per-stage failure -- every stage
    would fail the same way -- so it stops the run rather than being
    recorded N times and reported as "every stage failed".
    """
    try:
        return runner(list(cmd), capture_output=True)
    except FileNotFoundError as exc:
        raise GridRenderError(f"ffmpeg binary not found: {cmd[0]}") from exc


def _stderr_text(completed: subprocess.CompletedProcess) -> str:
    """ffmpeg's complaint, trimmed to its last 2000 useful characters.

    Decoded defensively: ``capture_output`` without ``text`` yields
    bytes, but a caller-supplied runner may hand back either.
    """
    raw = completed.stderr or completed.stdout or b""
    detail = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    return detail.strip()[-2000:] or "(no output)"


def render_grid_mp4(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    output_path: Path,
    canvas: GridCanvas | None = None,
    head_pad_seconds: float = 1.0,
    tail_pad_seconds: float = 0.5,
    layout_2up: Layout2Up = "horizontal",
    overlay: bool = False,
    overlay_theme: ThemeName = "splitsmith",
    summary_hold_seconds: float = 0.0,
    ffmpeg_binary: str | None = None,
    runner: Runner = subprocess.run,
    probe_runner: Runner = subprocess.run,
    still_runner: Runner = subprocess.run,
    rasterizer: Rasterizer | None = None,
    on_notice: NoticeHook | None = None,
    work_dir: Path | None = None,
) -> GridRenderResult:
    """Render every stage as a grid, then stitch them into one MP4.

    A stage whose ffmpeg call fails is recorded and skipped rather than
    ending the run: a full-match grid re-encode is far too long to lose
    to one bad stage. The stitch runs over whatever succeeded, and the
    caller reports failures from :attr:`GridRenderResult.failed`. Only
    when *every* stage fails does this raise -- there is nothing to
    concatenate and a zero-byte output would be worse than an error.

    ``ffmpeg_binary`` defaults to :func:`splitsmith.runtime.runtime`'s
    resolution rather than the literal ``"ffmpeg"``: the binary is not
    on PATH in a packaged app, and ``SPLITSMITH_FFMPEG`` has to win.

    ``overlay`` is off by default and turning it on changes nothing but
    the video half of each stage's filter graph: the shot data is read
    once for the whole run, rendered to sprite PNGs under
    ``work_dir/sprites`` and composited after ``xstack``. The stream
    layout, the track names and the audio graph are identical either way,
    which is what lets the stitch stream-copy the video.

    With ``overlay`` on, the ffmpeg that will do the work is asked what
    it can do *before any encoding starts*, because on a 12-stage 4K
    match the alternative is finding out an hour in with nothing to show
    for it. Two capabilities, two different answers:

    * **No ``drawtext``** (an ffmpeg built without ``--enable-libfreetype``,
      which many distro and static builds are) costs the running clock
      and nothing else -- the rest of the overlay is pre-rendered PNGs.
      So the clock is dropped, the overlay is kept, and the loss is
      reported through ``on_notice`` and in
      :attr:`GridRenderResult.degradations`.
    * **No concat ``option`` keyword** costs the overlay's timing
      outright, so ``--overlay`` is refused with
      :class:`GridRenderError` instead. The plain grid needs none of it
      and still renders on the same host.

    ``summary_hold_seconds`` freezes the grid at the end of every stage
    and holds each shooter's stage summary over their own cell for that
    long, inside the stage's own segment so the cross-stage stitch stays
    a stream copy. ``0.0``, the default, is the render this has always
    produced. It **requires** ``overlay``: the summary is drawn from the
    overlay's own shot data in the overlay's own typography, so a hold on
    a clean grid would be a blurred still with nothing written on it, and
    that is refused rather than rendered. See
    :data:`SUMMARY_HOLD_WARN_SECONDS` for the value a caller has almost
    certainly typo'd.

    ``probe_runner`` and ``still_runner`` are deliberately not ``runner``:
    both shipped callers count ``runner`` invocations to report "stage N
    of M", so anything else going through it misreports every stage.
    ``probe_runner`` asks the binary what it can do; ``still_runner``
    pulls one freeze frame per tile per stage for the summary. They are
    two parameters rather than one because a caller faking a capability
    probe is answering a completely different question from a caller
    faking a frame grab, and a fake that answers only the first would
    leave every summary cell black without saying so. ``on_notice``
    is how a caller says the degradation out loud at the moment it is
    decided; the same text is logged at warning level either way.

    ``rasterizer`` is the box-engine summary's own seam
    (:mod:`splitsmith.overlay_raster`), mirroring the ``Runner`` pattern
    above rather than a new one: a caller injects a fake here for the
    same reason it injects a fake ``still_runner``. Left ``None`` (the
    default), a hold that needs one gets a real
    :class:`~splitsmith.overlay_raster.ChromiumRasterizer`, opened once
    for the whole render -- not once per stage, since a 12-stage match
    would otherwise pay Chromium's process startup 12 times for an
    identical result -- and preflighted the same way the ffmpeg
    capability probe above is: attempting the real launch before any
    stage encodes, so a missing browser is found in the first second
    rather than after 11 stages have already rendered. A caller who
    passes their own ``rasterizer`` owns its lifecycle; this function
    only manages the one it creates itself. No usable Chromium degrades
    the same way no ``drawtext`` does -- reported through ``on_notice``
    and :attr:`GridRenderResult.degradations`, the render proceeds, and
    every hold still composes with the blurred freeze but no summary
    text, rather than failing the run. It never falls back to a second
    rendering engine.

    ``work_dir`` holds the per-stage segments and the concat list. It
    defaults to a directory beside the output -- same filesystem, so a
    match's worth of 4K segments doesn't have to fit in ``/tmp`` -- and
    is *not* cleaned up: the segments are what a failed stitch is
    debugged from, and skipping cleanup keeps a successful render from
    deleting a caller's own directory. Callers that want it gone should
    pass a path they own.
    """
    # Before the canvas, the binary or anything else: this is a caller
    # error, not a render outcome, and it costs nothing to say so first.
    if summary_hold_seconds > 0 and not overlay:
        raise GridRenderError(
            f"summary_hold_seconds={summary_hold_seconds:g} needs overlay=True (--overlay on the "
            "CLI). The end-of-stage hold freezes every tile and draws that shooter's stage "
            "summary over their own cell, which is the overlay's own shot data and typography; "
            "without it the hold is a blurred still with nothing written on it. Turn the overlay "
            "on, or leave summary_hold_seconds at 0."
        )

    canvas = canvas or GridCanvas()
    # Derivation keys off the rate fields, not off "no canvas given": a
    # caller who pinned only the geometry must still get the footage's
    # rate, and a caller who pinned a rate must get exactly that.
    if not canvas.is_frame_rate_pinned:
        canvas = canvas.with_frame_rate(*derive_frame_rate(shooters, audio_label=audio_label))
    binary = ffmpeg_binary or runtime().ffmpeg_binary
    plans = build_stage_plans(
        shooters,
        audio_label=audio_label,
        head_pad_seconds=head_pad_seconds,
        tail_pad_seconds=tail_pad_seconds,
        layout_2up=layout_2up,
        hold_seconds=summary_hold_seconds,
    )
    if not plans:
        raise GridRenderError("no stages to render -- no shooter has an exported trim")

    # The concat demuxer rejects segments whose stream layout differs, and
    # skipping a failed stage means the stitch list is not simply "all of
    # them". Every segment carries the mix plus one track per label, so
    # matching labels across plans is what pins the N+1 count. Every plan
    # from ``build_stage_plans`` carries one tile per label, so this holds
    # today; check it anyway rather than discover a future planner change
    # at the stitch, after the whole match has been encoded.
    labels = tuple(tile.label for tile in plans[0].tiles)
    for plan in plans[1:]:
        other = tuple(tile.label for tile in plan.tiles)
        if other != labels:
            raise GridRenderError(
                f"stage {plan.stage_number} has a different stream layout to stage "
                f"{plans[0].stage_number} ({', '.join(other)} vs {', '.join(labels)}); "
                "the concat demuxer cannot stitch those together"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    work = work_dir or output_path.parent / ".compare-grid-work"
    work.mkdir(parents=True, exist_ok=True)

    # Read once for the whole run, not per stage: every read opens the
    # shooter's project.json and each stage's audit, and a 12-stage
    # 4-shooter match would otherwise re-parse the same projects 12 times.
    overlay_data: Mapping[tuple[str, int], TileStageData] = {}
    font_path: Path | None = None
    degradations: tuple[OverlayDegradation, ...] = ()
    draw_clock = True
    if overlay:
        overlay_data = load_overlay_data(shooters)
        # One face for the whole overlay: the clock draws whatever the
        # sprite beside it resolved, so the two halves cannot diverge.
        # ``drawtext`` opens this path itself, long after this call, so it
        # has to be a real file on disk rather than a resource handle.
        font_path = overlay_font_file(theme_font_face(load_theme(overlay_theme)), work)
        # Everything above this line is pure planning and a file copy --
        # no ffmpeg has encoded anything yet, which is the whole point of
        # probing here. The font exists by now on purpose: it lets the
        # drawtext probe draw with the exact file the render would use.
        capabilities = ffmpeg_capabilities(binary, font_path=font_path, runner=probe_runner)
        if not capabilities.concat_option_keyword:
            raise GridRenderError(_concat_option_refusal(capabilities))
        if not capabilities.drawtext:
            draw_clock = False
            degradation = _drawtext_degradation(capabilities)
            degradations = (degradation,)
            # Said once, not twice. Measured: the CLI configures no
            # logging, so Python's last-resort handler sends WARNING and
            # above straight to stderr -- a ``logger.warning`` here as
            # well printed the whole paragraph twice, immediately above
            # the CLI's own "Note:". So the hook is the user-facing
            # channel when there is one and the log keeps the record;
            # with no hook the log has to raise its voice, because
            # nothing else is going to say it.
            if on_notice is not None:
                logger.info("%s", degradation.detail)
                on_notice(degradation.detail)
            else:
                logger.warning("%s", degradation.detail)

    # The rasterizer preflight, mirroring the drawtext capability probe
    # above: attempted once for the whole render rather than once per
    # stage -- both for cost (a 12-stage match would otherwise pay
    # Chromium's process startup 12 times) and so a missing browser is
    # found before any stage has encoded anything, the same reason the
    # drawtext probe runs up front. A caller-supplied ``rasterizer`` is
    # used as-is and its lifecycle is the caller's, not managed here.
    #
    # Gated on ``overlay``, not on ``summary_hold_seconds``: since issue
    # #693 the per-tile sprites are rasterized through the same browser
    # the summary is, so ``--overlay`` alone needs one even with no hold
    # requested. Getting this gate wrong is silent -- a hold-less render
    # would simply find ``rasterizer is None`` per stage and degrade every
    # sprite to a blank canvas without anything having failed.
    active_rasterizer: Rasterizer | None = rasterizer
    owned_rasterizer: ChromiumRasterizer | None = None
    if overlay and rasterizer is None:
        owned_rasterizer = ChromiumRasterizer()
        try:
            active_rasterizer = owned_rasterizer.__enter__()
        except RasterizerUnavailableError as exc:
            owned_rasterizer = None
            active_rasterizer = None
            degradations = degradations + (OverlayDegradation(summary=exc.summary, detail=exc.detail),)
            if on_notice is not None:
                logger.info("%s", exc.detail)
                on_notice(exc.detail)
            else:
                logger.warning("%s", exc.detail)

    outcomes: list[StageOutcome] = []
    segments: list[Path] = []
    try:
        for plan in plans:
            segment = work / f"stage{plan.stage_number}{SEGMENT_SUFFIX}"
            stage_overlay: StageOverlayPlan | None = None
            hold_still: Path | None = None
            # ``font_path`` is set exactly when ``overlay`` is; naming both
            # keeps that obvious rather than asserting it.
            if overlay and font_path is not None:
                stage_overlay = _stage_overlay_plan(
                    plan,
                    canvas,
                    overlay_data,
                    theme_name=overlay_theme,
                    font_path=font_path,
                    head_pad_seconds=head_pad_seconds,
                    work=work,
                    rasterizer=active_rasterizer,
                )
                if not draw_clock:
                    # Dropping the clocks is what removes ``drawtext`` from
                    # the command: ``_clock_filters`` emits one filter per
                    # clock and, with none, emits nothing and hands its own
                    # input label straight back, so the rest of the video
                    # chain composes onto ``[ovlgrid]`` unchanged.
                    stage_overlay = replace(stage_overlay, clocks=())
                if plan.hold_seconds > 0:
                    # A missing ``drawtext`` costs the summary nothing: the
                    # clock is a separate ffmpeg filter-graph chain (see
                    # ``_clock_filters``) that no longer draws once the
                    # action ends, and the summary's own text is composed
                    # through headless Chromium rasterizing CSS
                    # (``overlay_html``/``overlay_raster``, issue #683's
                    # amendment -- neither PIL nor drawtext), so a host that
                    # lost the clock still gets full summaries. A missing
                    # rasterizer (see the preflight above) costs the summary
                    # its text but not the still itself -- ``build_hold_still``
                    # composes the blurred freeze either way.
                    #
                    # Caught, not raised, and for the same reason a failed
                    # ffmpeg call below is: one bad stage is reported and
                    # skipped so the rest still stitch. ``overlay_summary``
                    # already degrades an unreadable trim or a bad freeze to
                    # a black cell, so what reaches here is the whole-stage
                    # kind -- a font that will not load, a disk that will not
                    # take the PNG. Building the segment anyway is not an
                    # option: without the still the stage's audio would
                    # outlast its video, which is the one fault nothing
                    # downstream reports.
                    try:
                        hold_still = _stage_hold_still(
                            plan,
                            canvas,
                            overlay_data,
                            theme_name=overlay_theme,
                            work=work,
                            ffmpeg_binary=binary,
                            runner=still_runner,
                            rasterizer=active_rasterizer,
                        )
                    except Exception as exc:  # noqa: BLE001 -- one bad stage must not lose the match
                        detail = f"could not compose the stage summary still: {exc}"
                        logger.warning("compare grid stage %d: %s", plan.stage_number, detail)
                        outcomes.append(
                            StageOutcome(
                                stage_number=plan.stage_number,
                                stage_name=plan.stage_name,
                                ok=False,
                                error=detail,
                            )
                        )
                        continue
            cmd = build_stage_command(
                plan,
                canvas=canvas,
                output_path=segment,
                ffmpeg_binary=binary,
                overlay=stage_overlay,
                hold_still_path=hold_still,
            )
            completed = _run_ffmpeg(cmd, runner=runner)
            if completed.returncode != 0:
                outcomes.append(
                    StageOutcome(
                        stage_number=plan.stage_number,
                        stage_name=plan.stage_name,
                        ok=False,
                        error=_stderr_text(completed),
                    )
                )
                continue
            segments.append(segment)
            outcomes.append(StageOutcome(stage_number=plan.stage_number, stage_name=plan.stage_name, ok=True))
    finally:
        # Closed as soon as the stage loop is done, not held open through
        # the final concat/stitch below -- the stitch is ffmpeg-only and
        # never touches the rasterizer. A caller-supplied ``rasterizer``
        # is left alone: its lifecycle was never this function's to manage.
        if owned_rasterizer is not None:
            owned_rasterizer.__exit__(None, None, None)

    if not segments:
        raise GridRenderError(
            f"every stage failed to render ({len(outcomes)} attempted); nothing to stitch. "
            f"Last error: {outcomes[-1].error}"
        )

    list_path = work / "concat.txt"
    list_path.write_text(
        "".join(f"file '{segment.resolve().as_posix()}'\n" for segment in segments),
        encoding="utf-8",
    )
    # The labels and the default flag have to be restated here: stream
    # copy carries neither across the concat demuxer, so without this the
    # finished file offers N+1 anonymous tracks.
    concat_cmd = build_concat_command(
        list_path=list_path,
        output_path=output_path,
        ffmpeg_binary=binary,
        audio_labels=labels,
    )
    completed = _run_ffmpeg(concat_cmd, runner=runner)
    if completed.returncode != 0:
        raise GridRenderError(f"concat stitch failed: {_stderr_text(completed)}")

    return GridRenderResult(
        output_path=output_path,
        stages=tuple(outcomes),
        degradations=degradations,
    )


__all__ = [
    "DEFAULT_CANVAS_HEIGHT",
    "DEFAULT_CANVAS_WIDTH",
    "FALLBACK_FRAME_RATE_DEN",
    "FALLBACK_FRAME_RATE_NUM",
    "MIX_NORMALIZE",
    "MIX_TRACK_LABEL",
    "OUTPUT_AUDIO_BITRATE",
    "OUTPUT_AUDIO_CODEC",
    "OVERLAY_CLOCK_FALLBACK_FONT",
    "OVERLAY_CLOCK_OMITTED_SUMMARY",
    "SEGMENT_AUDIO_CODEC",
    "SEGMENT_SUFFIX",
    "SUMMARY_HOLD_WARN_SECONDS",
    "GridCanvas",
    "GridRenderError",
    "GridRenderResult",
    "GridStagePlan",
    "GridTile",
    "NoticeHook",
    "OverlayDegradation",
    "StageOutcome",
    "StageOverlayPlan",
    "TileClock",
    "audio_track_labels",
    "build_concat_command",
    "build_stage_command",
    "build_stage_plans",
    "derive_frame_rate",
    "render_grid_mp4",
]
