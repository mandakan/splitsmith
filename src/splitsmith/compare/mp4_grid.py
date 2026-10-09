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
from typing import Literal

from ..composition import (
    MatchTitle,
    TitleCard,
    TitleStyle,
    Transition,
    TransitionKind,
    is_sting,
    sting_name,
    xfade_name,
)
from ..export_naming import stage_display_name
from ..identity import ResolvedIdentity
from ..look_motion import MotionClipError, motion_overlay_filters, write_motion_clip
from ..look_sting import sting_motion, sting_overlay_filters
from ..looks import CardSlot, Look, load_look, sting_template_for
from ..overlay_card import (
    CardMotion,
    card_backdrop,
    card_motion,
    compose_card,
    first_frame_image,
    lower_third_clip_filters,
    lower_third_filters,
    with_card_failures,
)
from ..overlay_clock import clock_common_options, clock_text, elapsed_text_option
from ..overlay_layout import Anchor, CellScale, anchor_ffmpeg_expr
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..overlay_text import FALLBACK_BUNDLED_FONT, overlay_font_file
from ..overlay_theme import OverlayTheme, ThemeName, load_theme, theme_for
from ..runtime import FFmpegCapabilities, ffmpeg_capabilities, quote_filter_value, runtime
from ..segment_cache import SegmentCache
from ..youtube_sidecar import Chapter
from .free_cell import (
    FreeCellContext,
    FreeCellKind,
    build_free_cell_still,
    free_cell_groups,
    row_gutter,
    surface_still,
)
from .layout import Layout2Up, choose_grid, grid_shape
from .overlay_data import TileStageData, load_expected_rounds, load_overlay_data
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
    #: Another of the shooter's cameras small in a corner of the cell
    #: (2026-10-02), lined up on the beep the same way: its own seek and
    #: lead pad put its beep at ``head_pad_seconds`` too. ``None`` when the
    #: tile has no inset on this stage.
    inset_path: Path | None = None
    inset_seek_seconds: float = 0.0
    inset_lead_pad_seconds: float = 0.0


#: Corner of the cell an inset sits in, and its width as a share of the cell's.
InsetCorner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]
DEFAULT_INSET_SCALE = 0.30


@dataclass(frozen=True)
class GridInset:
    """Where the tiles' insets sit; one look for the whole grid."""

    corner: InsetCorner = "bottom-right"
    scale: float = DEFAULT_INSET_SCALE


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


# --- the spine and its boundaries (issue #1244) ----------------------------------------


@dataclass(frozen=True)
class GridStageItem:
    """One stage's segment on the spine: the action and its hold.
    ``head_cut_seconds`` / ``tail_cut_seconds`` are what a transition on
    either side took off; the item is encoded that much shorter and the
    boundary segment shows them."""

    index: int
    plan: GridStagePlan
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0
    kind: Literal["stage"] = "stage"

    @property
    def name(self) -> str:
        return f"stage{self.plan.stage_number}"

    @property
    def duration_seconds(self) -> float:
        return self.plan.total_seconds - self.head_cut_seconds - self.tail_cut_seconds


@dataclass(frozen=True)
class GridCardItem:
    """A generated card segment on the spine. ``stage_index`` is the stage a
    slate precedes (and whose head frame backs it); the title page and
    the closing card carry the index of the stage their backdrop comes
    from. ``card`` is the match title for the title page and the closing
    card; a slate's card is built by the driver from the plan, the match
    summary's still from every stage's data (``card`` stays ``None``)."""

    kind: Literal["title_page", "slate", "match_summary", "closing"]
    name: str
    card_seconds: float
    stage_index: int | None
    card: MatchTitle | None = None
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.card_seconds - self.head_cut_seconds - self.tail_cut_seconds


GridItem = GridStageItem | GridCardItem


@dataclass(frozen=True)
class GridBoundary:
    """A transition between ``items[after_index]`` and the next item: a
    crossfade of ``duration_seconds`` centred on the cut, each neighbour
    having given up half of it, so the spine keeps its length."""

    after_index: int
    kind: TransitionKind
    duration_seconds: float

    @property
    def name(self) -> str:
        return f"boundary-{self.after_index:03d}"


@dataclass(frozen=True)
class GridSpine:
    items: tuple[GridItem, ...]
    boundaries: tuple[GridBoundary, ...] = ()
    degradations: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        return sum(i.duration_seconds for i in self.items) + sum(b.duration_seconds for b in self.boundaries)

    def boundary_after(self, index: int) -> GridBoundary | None:
        for boundary in self.boundaries:
            if boundary.after_index == index:
                return boundary
        return None


def head_pad_of(plan: GridStagePlan) -> float:
    """The head pad the plan was built with, recovered from any real tile
    through the tile invariant ``lead + (beep - seek) == head_pad``; 0 when
    every tile is filler (nothing to place)."""
    for tile in plan.tiles:
        if tile.trim_path is not None:
            return tile.lead_pad_seconds + tile.beep_offset_in_clip - tile.seek_seconds
    return 0.0


def grid_boundary_fit(
    prev: GridItem, nxt: GridItem, *, half: float, seconds: float, tail_pad_seconds: float
) -> str | None:
    """Why a transition of ``seconds`` cannot sit between ``prev`` and
    ``nxt``, or ``None``. A stage before the cut gives up ``half`` of its
    hold and tail pad (the last shot stays out of the fade); one after
    gives up ``half`` of its head pad (the beep stays out); a card gives up
    ``half`` of itself and must be at least ``2 * half`` long. Reports,
    never clamps; the handle past the pad is never a reason (the
    boundary holds a frame for what the trims lack)."""
    if isinstance(prev, GridStageItem):
        room = prev.plan.hold_seconds + tail_pad_seconds
        if half > room:
            return (
                f"transition after stage {prev.plan.stage_name!r} ({seconds:g}s) exceeds the stage's hold "
                f"and tail pad ({room:g}s); lengthen the hold or the pad, or shorten the transition: "
                "rendered as a cut"
            )
    elif half > prev.duration_seconds / 2.0:
        return (
            f"transition after {prev.name} ({seconds:g}s) exceeds half the card "
            f"({prev.duration_seconds / 2.0:g}s): rendered as a cut"
        )
    if isinstance(nxt, GridStageItem):
        pad = head_pad_of(nxt.plan)
        if half > pad:
            return (
                f"transition before stage {nxt.plan.stage_name!r} ({seconds:g}s) exceeds the stage's "
                f"head pad ({pad:g}s); increase the pad or shorten the transition: rendered as a cut"
            )
    elif half > nxt.duration_seconds / 2.0:
        return (
            f"transition into {nxt.name} ({seconds:g}s) exceeds half the card "
            f"({nxt.duration_seconds / 2.0:g}s): rendered as a cut"
        )
    return None


def plan_grid_spine(
    plans: Sequence[GridStagePlan],
    *,
    title_page: MatchTitle | None,
    closing: MatchTitle | None,
    stage_titles: str,
    title_duration_seconds: float,
    transitions: Sequence[Transition],
    tail_pad_seconds: float,
    match_summary_seconds: float = 0.0,
) -> GridSpine:
    """The spine the driver walks (issue #1244): title page, per stage a
    slate when ``stage_titles == "slate"`` and the stage, the match summary
    when ``match_summary_seconds`` is positive, the closing card;
    then the stage-indexed ``transitions`` placed as boundaries between
    the last item of stage i (the stage, hold included) and the first of
    stage i+1 (its slate when it has one). A transition that does not fit
    is reported and left out."""
    items: list[GridItem] = []
    if title_page is not None:
        items.append(
            GridCardItem(
                kind="title_page",
                name="title_page",
                card_seconds=title_page.duration_seconds,
                stage_index=0,
                card=title_page,
            )
        )
    first_of_stage: dict[int, int] = {}
    last_of_stage: dict[int, int] = {}
    for index, plan in enumerate(plans):
        if stage_titles == "slate":
            first_of_stage[index] = len(items)
            items.append(
                GridCardItem(
                    kind="slate",
                    name=f"slate-stage{plan.stage_number}",
                    card_seconds=title_duration_seconds,
                    stage_index=index,
                )
            )
        first_of_stage.setdefault(index, len(items))
        last_of_stage[index] = len(items)
        items.append(GridStageItem(index=index, plan=plan))
    if match_summary_seconds > 0 and plans:
        items.append(
            GridCardItem(
                kind="match_summary",
                name="match_summary",
                card_seconds=match_summary_seconds,
                stage_index=len(plans) - 1,
            )
        )
    if closing is not None:
        items.append(
            GridCardItem(
                kind="closing",
                name="closing",
                card_seconds=closing.duration_seconds,
                stage_index=len(plans) - 1,
                card=closing,
            )
        )
    boundaries: list[GridBoundary] = []
    degradations: list[str] = []
    for transition in sorted(transitions, key=lambda t: t.from_stage_index):
        prev_index = last_of_stage.get(transition.from_stage_index)
        next_index = first_of_stage.get(transition.to_stage_index)
        if prev_index is None or next_index is None or next_index != prev_index + 1:
            continue
        half = transition.duration_seconds / 2.0
        problem = grid_boundary_fit(
            items[prev_index],
            items[next_index],
            half=half,
            seconds=transition.duration_seconds,
            tail_pad_seconds=tail_pad_seconds,
        )
        if problem is not None:
            degradations.append(problem)
            continue
        items[prev_index] = replace(items[prev_index], tail_cut_seconds=half)
        items[next_index] = replace(items[next_index], head_cut_seconds=half)
        boundaries.append(
            GridBoundary(
                after_index=prev_index, kind=transition.kind, duration_seconds=transition.duration_seconds
            )
        )
    return GridSpine(items=tuple(items), boundaries=tuple(boundaries), degradations=tuple(degradations))


def narrow_grid_plan(plan: GridStagePlan, *, head_cut: float, tail_cut: float) -> GridStagePlan:
    """The same stage ``head_cut`` seconds later in and ``tail_cut`` shorter
    (issue #1244). The head pad shrinks by the cut and every real tile's
    seek and lead pad (and its inset's) are rebuilt from it, so the beep
    stays on the pad; the tail cut comes out of the hold first, then the
    action. Negative cuts widen the window (an edge's handle). Zero cuts
    return an equal plan."""
    if head_cut == 0.0 and tail_cut == 0.0:
        return plan
    old_pad = head_pad_of(plan)
    new_pad = old_pad - head_cut
    tiles: list[GridTile] = []
    for tile in plan.tiles:
        if tile.trim_path is None:
            tiles.append(tile)
            continue
        if tile.inset_path is not None:
            # The inset's own beep, recovered through the same invariant.
            inset_beep = old_pad - tile.inset_lead_pad_seconds + tile.inset_seek_seconds
            inset_seek = max(0.0, inset_beep - new_pad)
            inset_lead = max(0.0, new_pad - inset_beep)
        else:
            inset_seek, inset_lead = 0.0, 0.0
        tiles.append(
            replace(
                tile,
                seek_seconds=max(0.0, tile.beep_offset_in_clip - new_pad),
                lead_pad_seconds=max(0.0, new_pad - tile.beep_offset_in_clip),
                inset_seek_seconds=inset_seek,
                inset_lead_pad_seconds=inset_lead,
            )
        )
    hold_cut = max(0.0, min(tail_cut, plan.hold_seconds))
    duration = plan.duration_seconds - head_cut - (tail_cut - hold_cut)
    # A tile whose footage ended before the window would seek past its own
    # end and hand ffmpeg no frames; in the stage it was black from its end
    # on, so here it is filler: black, silent, the same inputs and track.
    tiles = [
        (
            replace(
                tile,
                trim_path=None,
                beep_offset_in_clip=0.0,
                seek_seconds=0.0,
                lead_pad_seconds=0.0,
                source_duration_seconds=0.0,
                inset_path=None,
                inset_seek_seconds=0.0,
                inset_lead_pad_seconds=0.0,
            )
            if tile.trim_path is not None and tile.seek_seconds >= tile.source_duration_seconds
            else tile
        )
        for tile in tiles
    ]
    return replace(
        plan, tiles=tuple(tiles), duration_seconds=duration, hold_seconds=plan.hold_seconds - hold_cut
    )


def overlay_plan_for(variant: GridStagePlan, *, source: GridStagePlan) -> GridStagePlan:
    """``variant`` (a narrowed or edge plan) with the tiles' presence taken
    from ``source``: a tile whose footage ended before the window is
    filler for ffmpeg, but its counters, splits and held clock are still
    on the overlay, as they were in the stage (review of #1244)."""
    tiles = tuple(
        replace(tile, trim_path=original.trim_path)
        for tile, original in zip(variant.tiles, source.tiles, strict=True)
    )
    return replace(variant, tiles=tiles)


def grid_edge_handle(plan: GridStagePlan, *, half: float, end: Literal["tail", "head"]) -> float:
    """How much footage past the pad an edge can read, up to ``half``: at
    the head the smallest seek among the real tiles (a lead-padded tile
    has none); at the tail the smallest footage past the action end, and
    none once a hold follows (the hold is a still, held by the boundary
    just the same). The boundary holds a frame for what is missing."""
    real = [tile for tile in plan.tiles if tile.trim_path is not None]
    if not real:
        return 0.0
    if end == "head":
        available = min(tile.seek_seconds for tile in real)
    elif plan.hold_seconds > 0.0:
        return 0.0
    else:
        available = min(
            tile.source_duration_seconds - (tile.seek_seconds + plan.duration_seconds - tile.lead_pad_seconds)
            for tile in real
        )
    return max(0.0, min(half, available))


def grid_edge_is_hold_only(plan: GridStagePlan, *, half: float) -> bool:
    """A tail edge that lies entirely in the hold: there is no action to
    render, the driver makes it a still of the hold PNG."""
    return plan.hold_seconds >= half


def grid_edge_plan(plan: GridStagePlan, *, half: float, end: Literal["tail", "head"]) -> GridStagePlan:
    """The stage window a boundary's edge render shows (issue #1244): at
    the head, the handle before the pad plus the first ``half``; at the
    tail, the last ``half`` (out of the hold first, then the action) plus
    the handle after it. Not defined for a hold-only tail edge
    (:func:`grid_edge_is_hold_only`)."""
    handle = grid_edge_handle(plan, half=half, end=end)
    if end == "head":
        return narrow_grid_plan(plan, head_cut=-handle, tail_cut=plan.total_seconds - half)
    if grid_edge_is_hold_only(plan, half=half):
        raise ValueError("a tail edge inside the hold is a still of the hold, not a stage plan")
    action_part = half - plan.hold_seconds
    return narrow_grid_plan(plan, head_cut=plan.duration_seconds - action_part, tail_cut=-handle)


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
            inset = bundle.inset
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
                    inset_path=inset.trim_path if inset is not None else None,
                    inset_seek_seconds=(
                        max(0.0, inset.beep_offset_in_clip - head_pad_seconds) if inset is not None else 0.0
                    ),
                    inset_lead_pad_seconds=(
                        max(0.0, head_pad_seconds - inset.beep_offset_in_clip) if inset is not None else 0.0
                    ),
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
                stage_name=stage_display_name(stage_number, stage_name),
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
        common = clock_common_options(
            font_path=overlay.font_path,
            font_size=overlay.font_size,
            ink=overlay.ink,
            stroke=overlay.stroke,
            x_expr=x_expr,
            y_expr=y_expr,
        )
        start = f"{clock.start_seconds:g}"
        elapsed = elapsed_text_option(start)
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


@dataclass(frozen=True)
class LowerThirdInput:
    """A rasterized lower-third (a composed-size PNG, or with ``clip`` an
    alpha clip from ``look_motion``) and how long it shows."""

    path: Path
    seconds: float
    clip: bool = False
    #: Issue #1244: the window opens ``delay_seconds`` late (a boundary's
    #: head edge) or drops ``skip_seconds`` already shown (the trimmed stage
    #: after that boundary). See ``overlay_card.lower_third_filters``.
    delay_seconds: float = 0.0
    skip_seconds: float = 0.0

    @property
    def shown_seconds(self) -> float:
        """How long the looped PNG input must last: until the window closes."""
        return self.delay_seconds + self.seconds - self.skip_seconds


StageTitleKind = Literal["none", "slate", "lower-third"]


def stage_card(
    plan: GridStagePlan,
    *,
    style: TitleStyle,
    seconds: float,
    expected_rounds: int | None,
    variant: str = "default",
) -> TitleCard:
    """The generated card for one grid stage (issue #973): the stage name,
    with its round count as an info line when known -- the same shape the
    single-shooter export builds, so the two products read alike.
    ``variant`` names the Look template variant that draws it (#1242)."""
    return TitleCard(
        text=plan.stage_name,
        duration_seconds=seconds,
        style=style,
        info=(f"{expected_rounds} rounds",) if expected_rounds else (),
        variant=variant,
    )


def build_card_segment_command(
    png_path: Path,
    *,
    seconds: float,
    canvas: GridCanvas,
    shooter_labels: Sequence[str],
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    motion_clip: Path | None = None,
    clip_offset_seconds: float = 0.0,
    clip_delay_seconds: float = 0.0,
) -> tuple[str, ...]:
    """Hold one composed-size PNG for ``seconds`` as a segment with the
    grid's own stream layout (issue #973). With ``motion_clip`` (#1242)
    the PNG is the card's backdrop and the clip, a template's alpha
    frames, is laid over it with its last frame held to ``seconds``.

    A card is its own segment rather than a head extension of a stage's
    graph so the title page and the closing card do not have to hang off
    whichever stage happens to be first or last -- and so a failed stage
    does not take the match's title with it. The price is the layout
    rule :func:`build_stage_command` pins: one video plus the mix and
    one track per shooter, all PCM, named and flagged the same way, or
    the concat demuxer refuses the stitch. ``anullsrc`` split N+1 ways
    is exactly that. ``-t`` is an output option: both inputs are
    infinite and the hold is what bounds them.
    """
    rate = canvas.rate_string
    slots = len(shooter_labels)
    fan_out = "".join(f"[a{slot}]" for slot in range(slots))
    audio_index = 1
    video_parts = ["[0:v]format=yuv420p,setsar=1[final]"]
    if motion_clip is not None:
        audio_index = 2
        motion_parts, label = motion_overlay_filters(
            1,
            rate=rate,
            seconds=seconds,
            source_label="0:v",
            delay_seconds=clip_delay_seconds,
            offset_seconds=clip_offset_seconds,
        )
        video_parts = [*motion_parts, f"[{label}]format=yuv420p,setsar=1[final]"]
    graph = ";".join(
        [
            *video_parts,
            f"[{audio_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"asplit={slots + 1}[amix]{fan_out}",
        ]
    )
    args: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loop",
        "1",
        "-framerate",
        rate,
        "-i",
        str(png_path),
    ]
    if motion_clip is not None:
        args += ["-i", str(motion_clip)]
    args += [
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",
        f"{seconds:g}",
        "-filter_complex",
        graph,
        "-map",
        "[final]",
        "-map",
        "[amix]",
    ]
    for slot in range(slots):
        args += ["-map", f"[a{slot}]"]
    track_labels = audio_track_labels(shooter_labels)
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
        "-c:a",
        SEGMENT_AUDIO_CODEC,
        str(output_path),
    ]
    return tuple(args)


def build_boundary_segment_command(
    tail_edge: Path,
    head_edge: Path,
    *,
    kind: TransitionKind,
    seconds: float,
    canvas: GridCanvas,
    shooter_labels: Sequence[str],
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    tail_pad_seconds: float = 0.0,
    head_pad_seconds: float = 0.0,
    sting_clip: Path | None = None,
) -> tuple[str, ...]:
    """The boundary segment (issue #1244): ``tail_edge`` crossfaded into
    ``head_edge`` over ``seconds`` with ``xfade``, and every one of the
    grid's N+1 audio tracks (the mix, then one per shooter, a filler's
    being silence) crossfaded by stream index with ``acrossfade``, so the
    stitch sees the layout, names and dispositions every other segment
    carries. An edge whose trims had less handle than ``seconds / 2`` is
    shorter; ``tail_pad_seconds`` holds its last frame (silence on every
    track) and ``head_pad_seconds`` holds the head edge's first frame
    (delaying every track) so both inputs span the fade.

    ``sting_clip`` (issue #1245) is the sting's alpha clip, a third input
    laid over the crossfaded video for the whole boundary before the
    final pixel format; without it the argv is exactly what it was."""
    rate = canvas.rate_string
    tracks = len(shooter_labels) + 1
    parts: list[str] = []
    tail_v, head_v = "0:v", "1:v"
    if tail_pad_seconds > 0.0:
        parts.append(f"[0:v]tpad=stop_mode=clone:stop_duration={tail_pad_seconds:g}[tv]")
        tail_v = "tv"
    if head_pad_seconds > 0.0:
        parts.append(f"[1:v]tpad=start_mode=clone:start_duration={head_pad_seconds:g}[hv]")
        head_v = "hv"
    xfade = f"[{tail_v}][{head_v}]xfade=transition={xfade_name(kind)}:duration={seconds:g}:offset=0"
    inputs = ["-i", str(tail_edge), "-i", str(head_edge)]
    if sting_clip is None:
        parts.append(f"{xfade},format=yuv420p[final]")
    else:
        sting_parts, stung = sting_overlay_filters(2, rate=rate, seconds=seconds, source_label="xf")
        parts += [f"{xfade}[xf]", *sting_parts, f"[{stung}]format=yuv420p[final]"]
        inputs += ["-i", str(sting_clip)]
    for k in range(tracks):
        tail_a, head_a = f"0:a:{k}", f"1:a:{k}"
        if tail_pad_seconds > 0.0:
            parts.append(f"[0:a:{k}]apad=pad_dur={tail_pad_seconds:g}[t{k}]")
            tail_a = f"t{k}"
        if head_pad_seconds > 0.0:
            parts.append(f"[1:a:{k}]adelay={round(head_pad_seconds * 1000)}:all=1[h{k}]")
            head_a = f"h{k}"
        parts.append(
            f"[{tail_a}][{head_a}]acrossfade=d={seconds:g}:c1=tri:c2=tri,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[x{k}]"
        )
    args: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        *inputs,
        "-t",
        f"{seconds:g}",
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[final]",
    ]
    for k in range(tracks):
        args += ["-map", f"[x{k}]"]
    track_labels = audio_track_labels(shooter_labels)
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
        "-c:a",
        SEGMENT_AUDIO_CODEC,
        str(output_path),
    ]
    return tuple(args)


def build_stage_command(
    plan: GridStagePlan,
    *,
    canvas: GridCanvas,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    overlay: StageOverlayPlan | None = None,
    hold_still_path: Path | None = None,
    lower_third: LowerThirdInput | None = None,
    inset: GridInset | None = None,
    free_cell_still: Path | None = None,
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
    # Every tile takes two inputs -- a real one its trim twice (video
    # seeked, audio not), a filler its colour and its anullsrc -- so a
    # tile's slot is never its input index; the graph is built from
    # these lists.
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
            next_index += 1
            # The audio is *not* taken from that seeked input. Input-side
            # ``-ss`` is exact for video (keyframe seek, then decode and
            # discard to the target) but not for the audio of a
            # stream-copied trim: measured on a real match in the
            # single-shooter renderer (``mp4_render``), one stage's audio
            # came out cut 0.42 s late while its video was cut exactly.
            # Here the ``apad,atrim`` clamp keeps the *length* honest, so
            # nothing accumulates across stages, but the tile's sound
            # would still sit 0.4 s early against its own picture for the
            # whole stage. The trim is opened a second time with no seek
            # and its audio cut by ``atrim`` on decoded timestamps, which
            # honour the trim's edit list. ``-t`` bounds the read to the
            # same window so the decoder stops where the cut does.
            args += [
                "-t",
                f"{tile.seek_seconds + plan.duration_seconds - tile.lead_pad_seconds:g}",
                "-i",
                str(tile.trim_path),
            ]
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
    # The first free square shows ``free_cell_still`` when there is one
    # (``compare/free_cell``): the same input position the black source
    # takes, so nothing behind it renumbers, and the same cell chain.
    empty_index: list[int] = []
    for cell_number, _cell in enumerate(_unreached_cells(plan)):
        if cell_number == 0 and free_cell_still is not None:
            args += [
                "-loop",
                "1",
                "-framerate",
                rate,
                "-t",
                f"{plan.duration_seconds:g}",
                "-i",
                str(free_cell_still),
            ]
        else:
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

    # After everything else, for the same reason everything else went
    # after its predecessor: the only index safe to occupy is the next
    # free one. Composited on the action (see ``_build_filter_graph``),
    # so a card can never reach a frame of the hold.
    lower_third_graph: tuple[int, float, bool, float, float] | None = None
    if lower_third is not None:
        if lower_third.clip:
            # A clip carries its own frames and length; the graph conforms
            # and holds it (``lower_third_clip_filters``).
            args += ["-i", str(lower_third.path)]
        else:
            args += [
                "-loop",
                "1",
                "-framerate",
                rate,
                "-t",
                f"{lower_third.shown_seconds:g}",
                "-i",
                str(lower_third.path),
            ]
        lower_third_graph = (
            next_index,
            lower_third.seconds,
            lower_third.clip,
            lower_third.delay_seconds,
            lower_third.skip_seconds,
        )
        next_index += 1

    # The tiles' insets, video only, dead last for the reason every input
    # above went after its predecessor: anything earlier renumbers the
    # streams behind it and moves a shooter's audio into another's track.
    # A plan with no insets adds nothing, so its argv does not move.
    inset_index: list[int | None] = []
    for tile in plan.tiles:
        if tile.inset_path is None:
            inset_index.append(None)
            continue
        args += [
            "-ss",
            f"{tile.inset_seek_seconds:g}",
            "-t",
            f"{plan.duration_seconds - tile.inset_lead_pad_seconds:g}",
            "-i",
            str(tile.inset_path),
        ]
        inset_index.append(next_index)
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
            lower_third=lower_third_graph,
            inset_index=inset_index,
            inset=inset,
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
    lower_third: tuple[int, float, bool, float, float] | None = None,
    inset_index: Sequence[int | None] = (),
    inset: GridInset | None = None,
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

    # Insets: the second camera scaled to a share of the cell's width and
    # laid over the finished tile in its corner, inside the tile -- after
    # the chain that puts the beep on ``head_pad``, never in it. Its own
    # chain has the same ``tpad`` / ``setpts`` / ``fps`` / ``trim`` order
    # for the same reason, so its beep lands where the tile's does.
    inset = inset or GridInset()
    tile_out = [f"t{slot}" for slot in range(len(plan.tiles))]
    margin = max(2, round(cell_w * 0.02))
    inset_w = max(2, round(cell_w * inset.scale / 2) * 2)
    x = f"{margin}" if inset.corner.endswith("left") else f"W-w-{margin}"
    y = f"{margin}" if inset.corner.startswith("top") else f"H-h-{margin}"
    for slot, tile in enumerate(plan.tiles):
        source = inset_index[slot] if slot < len(inset_index) else None
        if source is None:
            continue
        lead = (
            f"tpad=start_duration={tile.inset_lead_pad_seconds:g}:start_mode=add:color=black,"
            if tile.inset_lead_pad_seconds > 0
            else ""
        )
        parts.append(
            f"[{source}:v]{lead}setpts=PTS-STARTPTS,"
            f"scale={inset_w}:-2,setsar=1,fps={rate},"
            f"tpad=stop_duration={plan.duration_seconds:g}:stop_mode=add:color=black,"
            f"trim=0:{plan.duration_seconds:g}[n{slot}]"
        )
        parts.append(f"[t{slot}][n{slot}]overlay=x={x}:y={y}[u{slot}]")
        tile_out[slot] = f"u{slot}"

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

    stack_inputs = "".join(f"[{label}]" for label in tile_out)
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

    # The lower-third (issue #973) is the last thing drawn on the action
    # and sits upstream of the tail's ``concat``, like everything else
    # that draws on the action: there is no expression to get wrong.
    if lower_third is not None:
        lt_index, lt_seconds, lt_clip, lt_delay, lt_skip = lower_third
        if lt_clip:
            lt_parts, video_label = lower_third_clip_filters(
                lt_index,
                lt_seconds,
                rate=canvas.rate_string,
                source_label=video_label,
                delay_seconds=lt_delay,
                skip_seconds=lt_skip,
            )
        else:
            lt_parts, video_label = lower_third_filters(
                lt_index, lt_seconds, source_label=video_label, delay_seconds=lt_delay, skip_seconds=lt_skip
            )
        parts.extend(lt_parts)

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
        # A real tile's audio input is unseeked (see
        # ``build_stage_command``): ``atrim`` cuts the same window the
        # video's ``-ss`` / ``-t`` describe, on decoded timestamps, before
        # the re-base. A filler's ``anullsrc`` starts at zero and the
        # cut is a no-op on it.
        delay_ms = int(round(tile.lead_pad_seconds * 1000))
        lead = f"adelay={delay_ms}:all=1," if delay_ms > 0 else ""
        cut = (
            f"atrim=start={tile.seek_seconds:g}:duration={plan.duration_seconds - tile.lead_pad_seconds:g},"
            if tile.trim_path is not None
            else ""
        )
        parts.append(
            f"[{audio_index[slot]}:a]{cut}asetpts=PTS-STARTPTS,{lead}aresample=async=1,"
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
    #: One per rendered stage at the stage's first frame (its slate when
    #: it has one), anchored at 0:00 by a chapter named after the match
    #: when a title page comes first -- YouTube ignores a chapter list
    #: that does not start at 0:00. Built from the planned durations as
    #: the segments are laid down, the same spine the stitch plays.
    chapters: tuple[Chapter, ...] = ()

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
    tiles: bool = True,
    race_at: tuple[int, int] | None = None,
    list_suffix: str = "",
) -> StageOverlayPlan:
    """Render one stage's sprites and describe its clocks.

    ``tiles`` false is a render whose only live element is the free
    square's race (``race_at``): the sprites carry the race alone and no
    tile gets a clock, so the tiles stay as clean as with the overlay off.

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
        tiles=tiles,
        race_at=race_at,
        stage_number=plan.stage_number,
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
        work / f"sprites-stage{plan.stage_number}{list_suffix}.txt",
        frame_rate=canvas.frame_rate,
    )

    clocks: list[TileClock] = []
    for tile in plan.tiles if tiles else ():
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
                final_text=clock_text(last),
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
    identities: Mapping[str, ResolvedIdentity] | None = None,
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
        accents={label: ident.accent for label, ident in (identities or {}).items()},
    )


#: How far before a *tail* backdrop's target the grab starts reading, and
#: how much it reads; ``-update 1`` keeps the last frame decoded. See
#: ``overlay_summary._FREEZE_TAIL_WINDOW_SECONDS`` for why a seek straight
#: to the last timestamp can come back empty. A *head* grab has no such
#: problem -- there is always a frame at or after the seek -- so it takes
#: exactly the first one (``-frames:v 1``); reading a window there would
#: keep a frame half a second *into* the stage instead.
_CARD_BACKDROP_WINDOW_SECONDS = 0.5


def _grab_card_backdrop(
    plan: GridStagePlan,
    *,
    at: Literal["head", "tail"],
    name: str,
    work: Path,
    ffmpeg_binary: str,
    runner: Runner,
) -> Path | None:
    """One frame of the audio-source shooter's trim for a card to sit on:
    its first frame for a card that precedes the stage, its last for the
    closing card. ``None`` when that shooter has no trim on this stage or
    the grab produced no file -- the card then composes flat, which is a
    picture, not a failure."""
    tile = next((t for t in plan.tiles if t.label == plan.audio_label and t.trim_path is not None), None)
    if tile is None:
        tile = next((t for t in plan.tiles if t.trim_path is not None), None)
    if tile is None or tile.trim_path is None:
        return None
    out = work / f"{name}_backdrop.png"
    out.unlink(missing_ok=True)
    if at == "head":
        window = ["-ss", f"{tile.seek_seconds:g}", "-i", str(tile.trim_path), "-an", "-frames:v", "1"]
    else:
        seek = max(0.0, tile.source_duration_seconds - _CARD_BACKDROP_WINDOW_SECONDS)
        window = [
            "-ss",
            f"{seek:g}",
            "-t",
            f"{_CARD_BACKDROP_WINDOW_SECONDS:g}",
            "-i",
            str(tile.trim_path),
            "-an",
            "-update",
            "1",
        ]
    cmd = [ffmpeg_binary, "-hide_banner", "-y", *window, str(out)]
    try:
        completed = runner(cmd, capture_output=True)
    except Exception as exc:  # noqa: BLE001 -- a card's backdrop is never worth the render
        logger.warning(
            "compare grid: could not grab a backdrop for %s (%s); the card composes flat", name, exc
        )
        return None
    if completed.returncode != 0:
        return None
    try:
        return out if out.stat().st_size > 0 else None
    except OSError:
        return None


@dataclass(frozen=True)
class _CardAssets:
    """What a card's encodes read: the composed still (or the backdrop,
    with the motion clip laid over it)."""

    png: Path
    motion_clip: Path | None = None


def _card_assets(
    card: MatchTitle | TitleCard,
    *,
    name: str,
    slot: CardSlot,
    plan: GridStagePlan,
    backdrop_at: Literal["head", "tail"],
    canvas: GridCanvas,
    look: Look | None,
    rasterizer: Rasterizer | None,
    work: Path,
    ffmpeg_binary: str,
    still_runner: Runner,
    identities: Mapping[str, ResolvedIdentity] | None = None,
) -> _CardAssets | None:
    """Compose one full-frame card's files; ``None`` when it was skipped --
    no rasterizer (already recorded as a degradation up front), a card
    whose text could not be rasterized, or a motion clip ffmpeg refused.
    Each is logged; none stops the render.

    Sized to the *composed* grid of ``plan``, not the canvas (#691): the
    stage segments are that size, and the stitch stream-copies video.
    """
    if rasterizer is None or look is None:
        return None
    composed_w, composed_h = _composed_size(canvas, plan)
    motion = card_motion(
        card,
        slot=slot,
        width=composed_w,
        height=composed_h,
        fps=canvas.fps,
        look=look,
        rasterizer=rasterizer,
        max_seconds=card.duration_seconds,
        shooters=_tile_identities(plan, identities),
    )
    if motion is None:
        return None
    backdrop = _grab_card_backdrop(
        plan, at=backdrop_at, name=name, work=work, ffmpeg_binary=ffmpeg_binary, runner=still_runner
    )
    canvas_image = card_backdrop(backdrop, width=composed_w, height=composed_h, look=look)
    if not motion.animated:
        text = first_frame_image(motion)
        if text is None:
            return None
        png = work / f"{name}.png"
        compose_card(text, canvas_image).save(png)
        return _CardAssets(png=png)
    backdrop_png = work / f"{name}_backdrop.png"
    canvas_image.save(backdrop_png)
    clip_path = work / f"{name}_motion.mov"
    try:
        write_motion_clip(motion.frames, out=clip_path, fps=canvas.fps, ffmpeg_binary=ffmpeg_binary)
    except MotionClipError as exc:
        logger.warning("compare grid: card %s could not be drawn and is skipped: %s", name, exc)
        return None
    finally:
        motion.close()
    return _CardAssets(png=backdrop_png, motion_clip=clip_path)


def _encode_card(
    assets: _CardAssets,
    *,
    seconds: float,
    name: str,
    plan: GridStagePlan,
    canvas: GridCanvas,
    ffmpeg_binary: str,
    runner: Runner,
    clip_offset_seconds: float = 0.0,
    clip_delay_seconds: float = 0.0,
    encoder: _GridEncoder | None = None,
) -> Path | None:
    """Encode ``assets`` as a ``seconds``-long segment named ``name``
    (through ``encoder``, so through the segment cache when it has one);
    ``None`` (logged) when ffmpeg refused."""
    work_path = assets.png.parent / f"{name}{SEGMENT_SUFFIX}"
    cmd = build_card_segment_command(
        assets.png,
        seconds=seconds,
        canvas=canvas,
        shooter_labels=tuple(tile.label for tile in plan.tiles),
        output_path=work_path,
        ffmpeg_binary=ffmpeg_binary,
        motion_clip=assets.motion_clip,
        clip_offset_seconds=clip_offset_seconds,
        clip_delay_seconds=clip_delay_seconds,
    )
    segment, error = (encoder or _GridEncoder(None, assets.png.parent)).encode(cmd, work_path, runner=runner)
    if segment is None:
        logger.warning("compare grid: card %s failed to encode and is skipped: %s", name, error)
    return segment


def _card_segment(
    card: MatchTitle | TitleCard,
    *,
    name: str,
    slot: CardSlot,
    plan: GridStagePlan,
    backdrop_at: Literal["head", "tail"],
    canvas: GridCanvas,
    look: Look | None,
    rasterizer: Rasterizer | None,
    work: Path,
    ffmpeg_binary: str,
    card_runner: Runner,
    still_runner: Runner,
    identities: Mapping[str, ResolvedIdentity] | None = None,
) -> Path | None:
    """Compose one full-frame card and encode it as a segment
    (:func:`_card_assets` then :func:`_encode_card`); ``None`` when either
    step skipped it."""
    assets = _card_assets(
        card,
        name=name,
        slot=slot,
        plan=plan,
        backdrop_at=backdrop_at,
        canvas=canvas,
        look=look,
        rasterizer=rasterizer,
        work=work,
        ffmpeg_binary=ffmpeg_binary,
        still_runner=still_runner,
        identities=identities,
    )
    if assets is None:
        return None
    return _encode_card(
        assets,
        seconds=card.duration_seconds,
        name=name,
        plan=plan,
        canvas=canvas,
        ffmpeg_binary=ffmpeg_binary,
        runner=card_runner,
    )


def _tile_identities(
    plan: GridStagePlan, identities: Mapping[str, ResolvedIdentity] | None
) -> tuple[ResolvedIdentity, ...]:
    """The resolved identities of this stage's tiles, in slot order, for
    the ones the caller resolved (#1243)."""
    if not identities:
        return ()
    return tuple(identities[tile.label] for tile in plan.tiles if tile.label in identities)


@dataclass(frozen=True)
class GridRenderStep:
    """One step of a grid render, for a progress line: a stage's segment
    (``plan``) being encoded or reused from the segment cache, or the
    stitch (``plan`` is ``None``). ``index`` counts stages from 0 in plan
    order and is ``total`` for the stitch. Cards, edges and boundaries
    are not steps; the CLIs' "stage N of M" counts stages."""

    index: int
    total: int
    plan: GridStagePlan | None
    status: Literal["encoding", "reused", "stitching"]


GridProgress = Callable[[GridRenderStep], None]


class _GridEncoder:
    """Runs a segment's ffmpeg command, or reuses the segment from the
    segment cache (:mod:`splitsmith.segment_cache`); with no cache it runs
    the command as written. ``keys_by_path`` is the key each segment came
    from, so a boundary keys on its edges' identities rather than on
    files the cache's own LRU touch re-dates."""

    def __init__(self, cache: SegmentCache | None, work: Path) -> None:
        self.cache = cache
        self.work = work
        self.used_keys: set[str] = set()
        self.keys_by_path: dict[Path, str] = {}

    def encode(
        self,
        cmd: tuple[str, ...],
        out: Path,
        *,
        runner: Runner,
        extra_inputs: Sequence[Path] = (),
        virtual_inputs: Mapping[str, str] | None = None,
        report: Callable[[Literal["encoding", "reused"]], None] | None = None,
    ) -> tuple[Path | None, str]:
        """The segment's path, or ``None`` and ffmpeg's complaint."""
        if self.cache is None:
            if report is not None:
                report("encoding")
            completed = _run_ffmpeg(cmd, runner=runner)
            return (out, "") if completed.returncode == 0 else (None, _stderr_text(completed))
        key = self.cache.key(
            cmd,
            output_path=out,
            work_dir=self.work,
            virtual_inputs=virtual_inputs,
            extra_inputs=extra_inputs,
        )
        self.used_keys.add(key)
        hit = self.cache.lookup(key, suffix=out.suffix)
        if hit is not None:
            if report is not None:
                report("reused")
            self.keys_by_path[hit] = key
            return hit, ""
        if report is not None:
            report("encoding")
        partial = self.cache.partial_path(key, suffix=out.suffix)
        target = str(out)
        try:
            completed = _run_ffmpeg(
                tuple(str(partial) if token == target else token for token in cmd), runner=runner
            )
            if completed.returncode != 0:
                return None, _stderr_text(completed)
            if not partial.is_file():
                return None, f"ffmpeg reported success but wrote no {out.name}"
            committed = self.cache.commit(partial, key, suffix=out.suffix)
            self.keys_by_path[committed] = key
            return committed, ""
        finally:
            partial.unlink(missing_ok=True)


def _overlay_inputs(overlay: StageOverlayPlan | None) -> tuple[Path, ...]:
    """The files a stage's overlay reads that are not argv tokens: every
    sprite its concat list names, and the clock's font (named inside the
    ``drawtext`` filter). The segment cache keys on their bytes."""
    if overlay is None:
        return ()
    sprites: list[Path] = []
    try:
        lines = overlay.sprite_list_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("file '") and line.endswith("'"):
            sprite = Path(line[len("file '") : -1])
            if sprite not in sprites:
                sprites.append(sprite)
    return (*sprites, overlay.font_path)


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


def _free_cell_still(
    plan: GridStagePlan,
    *,
    kind: FreeCellKind,
    canvas: GridCanvas,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
    work: Path,
    match_name: str,
    match_date: str | None,
    tiles: dict[str, TileStageData],
    expected_rounds: int | None,
) -> Path | None:
    """Compose this stage's free square at the cell's size; ``None`` keeps
    the black cell (nothing to say, or the text could not be drawn)."""
    rounds = next((t.stage_rounds for t in tiles.values() if t.stage_rounds is not None), None)
    context = FreeCellContext(
        stage_number=plan.stage_number,
        stage_name=plan.stage_name,
        match_name=match_name,
        match_date=match_date,
        shooters=tuple(t.label for t in plan.tiles),
        expected_rounds=expected_rounds,
        paper_targets=getattr(rounds, "paper_targets", None),
        steel_targets=getattr(rounds, "steel_targets", None),
        tiles=tiles,
    )
    cell_w, cell_h = _cell_size(canvas, plan)
    image = build_free_cell_still(
        free_cell_groups(kind, context, gutter=row_gutter(cell_h)),
        width=cell_w,
        height=cell_h,
        theme=theme,
        rasterizer=rasterizer,
    )
    if image is None:
        return None
    path = work / f"free-stage{plan.stage_number}.png"
    image.save(path)
    return path


def _grid_item_label(item: GridItem, stage_names: Sequence[str]) -> str:
    """What a sting template is told is on either side of its cut (issue
    #1245): a stage by its name, a match card by its text, a slate (whose
    card the driver builds later) by the name of the stage it opens."""
    if isinstance(item, GridStageItem):
        return item.plan.stage_name
    if item.card is not None:
        return item.card.text
    if item.stage_index is not None and 0 <= item.stage_index < len(stage_names):
        return stage_names[item.stage_index]
    return item.name


class _EdgeFailedError(Exception):
    """A boundary's edge or the boundary itself could not be rendered; the
    transition becomes a cut."""


@dataclass
class _GridPrep:
    """What an item's encodes read, made once (issue #1244): a card's
    files, or a stage's lower third, hold still and free-cell still."""

    assets: _CardAssets | None = None
    lower_third: LowerThirdInput | None = None
    hold_still: Path | None = None
    free_still: Path | None = None
    has_overlay: bool = False
    failed: str | None = None


def _grid_missing_handle(item: GridItem, *, half: float, end: Literal["tail", "head"]) -> float:
    """How much of a boundary edge the item could not supply from footage:
    zero for a card (its handle is its own frame) and for a hold-only tail
    edge rendered as a still of the hold; ``half`` minus the trims' handle
    for a stage."""
    if isinstance(item, GridStageItem):
        if end == "tail" and grid_edge_is_hold_only(item.plan, half=half):
            return 0.0
        return half - grid_edge_handle(item.plan, half=half, end=end)
    return 0.0


@with_card_failures(
    lambda r, notes: replace(
        r, degradations=(*r.degradations, *(OverlayDegradation(summary=n, detail=n) for n in notes))
    )
)
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
    card_runner: Runner = subprocess.run,
    boundary_runner: Runner = subprocess.run,
    rasterizer: Rasterizer | None = None,
    on_notice: NoticeHook | None = None,
    work_dir: Path | None = None,
    title_page: MatchTitle | None = None,
    closing: MatchTitle | None = None,
    stage_titles: StageTitleKind = "none",
    title_duration_seconds: float = 1.5,
    card_variant: str = "default",
    identities: Mapping[str, ResolvedIdentity] | None = None,
    inset: GridInset | None = None,
    free_cell: FreeCellKind = "blank",
    match_name: str = "",
    match_date: str | None = None,
    transitions: Sequence[Transition] = (),
    segment_cache: SegmentCache | None = None,
    progress: GridProgress | None = None,
    match_summary_seconds: float = 0.0,
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

    ``title_page`` / ``closing`` / ``stage_titles`` (issue #973) are the
    generated cards: a match title card first, a card per stage (a
    ``slate`` segment before it, or a ``lower-third`` over its head that
    adds no time), a closing card last. Each full-frame card is its own
    segment in the grid's stream layout (see
    :func:`build_card_segment_command`), encoded through ``card_runner``
    -- not ``runner``, which both shipped callers count to say "stage N
    of M" -- and composed by the same rasterizer the overlay uses, opened
    for the cards alone when the overlay is off. No usable browser
    degrades exactly as it does for the summary: every card skipped, the
    loss recorded once. The ffmpeg capability probe stays gated on the
    overlay; a card needs a browser, not ``drawtext``.

    ``work_dir`` holds the per-stage segments and the concat list. It
    defaults to a directory beside the output -- same filesystem, so a
    match's worth of 4K segments doesn't have to fit in ``/tmp`` -- and
    is *not* cleaned up: the segments are what a failed stitch is
    debugged from, and skipping cleanup keeps a successful render from
    deleting a caller's own directory. Callers that want it gone should
    pass a path they own.

    ``segment_cache`` keeps every encoded segment (stages, cards, edges,
    boundaries) by the content of its command, as the single-shooter
    render does: a second render of the same grid encodes nothing that
    did not change and the stitch reads the rest from the cache. The
    sprites and the clock's font reach the key as ``extra_inputs``
    (:func:`_overlay_inputs`); the rasterizer still draws them each run.
    ``progress`` hears each stage as it is encoded or reused, then the
    stitch; a reused stage never reaches ``runner``.

    ``match_summary_seconds`` above zero adds the match summary (spec
    2026-10-08-grid-match-summary-design): one card after the last stage,
    before the closing card, every shooter's match figures in their own
    slot over their own last frame. It reads the shot data itself, so it
    does not need ``overlay``; with no browser it is the frames alone.
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
    if segment_cache is not None:
        # The cache fingerprints a work file only through an absolute argv
        # token; a relative one would be keyed by its name alone.
        work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    # Read once for the whole run, not per stage: every read opens the
    # shooter's project.json and each stage's audit, and a 12-stage
    # 4-shooter match would otherwise re-parse the same projects 12 times.
    overlay_data: Mapping[tuple[str, int], TileStageData] = {}
    font_path: Path | None = None
    degradations: tuple[OverlayDegradation, ...] = ()
    draw_clock = True
    # The free square's live race rides the overlay's sprite stream, so it
    # needs the shot data, the font and the concat probe the overlay does,
    # but not ``drawtext``: with the overlay off nothing draws a clock.
    race = free_cell == "race" and any(_unreached_cells(p) for p in plans)
    sprites = overlay or race
    if sprites:
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
        if overlay and not capabilities.drawtext:
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
    cards_requested = title_page is not None or closing is not None or stage_titles != "none"
    # A sting (#1245) is a Look template drawn by the same browser.
    sting_requested = any(is_sting(t.kind) for t in transitions)
    # The match summary's text is drawn by the same browser.
    match_summary_requested = match_summary_seconds > 0
    # A free square with something to say is drawn by the same browser.
    free_requested = free_cell != "blank" and any(_unreached_cells(p) for p in plans)
    active_rasterizer: Rasterizer | None = rasterizer
    owned_rasterizer: ChromiumRasterizer | None = None
    if (
        overlay or cards_requested or free_requested or sting_requested or match_summary_requested
    ) and rasterizer is None:
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
    encoder = _GridEncoder(segment_cache, work)

    def report(
        index: int, plan: GridStagePlan | None, status: Literal["encoding", "reused", "stitching"]
    ) -> None:
        if progress is not None:
            progress(GridRenderStep(index=index, total=len(plans), plan=plan, status=status))

    card_look = load_look(overlay_theme) if cards_requested or sting_requested else None
    card_theme = theme_for(card_look) if card_look is not None else None
    free_theme = card_theme or (load_theme(overlay_theme) if free_requested else None)
    free_tiles = load_overlay_data(shooters) if free_requested and free_cell == "splits" else {}
    free_rounds = load_expected_rounds(shooters) if free_requested and free_cell == "stage" else {}
    # Read for the stage cards whether or not the overlay is on: the count
    # comes from project.json alone, so a slate without ``--overlay``
    # still prints it (a review of #973 caught it silently absent).
    expected_rounds = load_expected_rounds(shooters) if stage_titles != "none" else {}
    # The YouTube chapter list, laid down with the segments below.
    elapsed = 0.0
    chapters: list[Chapter] = []
    match_title = title_page.text if title_page is not None else None
    labels_tuple = tuple(labels)
    spine = plan_grid_spine(
        plans,
        title_page=title_page,
        closing=closing,
        stage_titles=stage_titles,
        title_duration_seconds=title_duration_seconds,
        transitions=transitions,
        tail_pad_seconds=tail_pad_seconds,
        match_summary_seconds=match_summary_seconds,
    )
    items: list[GridItem] = list(spine.items)
    live: dict[int, GridBoundary] = {b.after_index: b for b in spine.boundaries}
    transition_notes: list[OverlayDegradation] = [
        OverlayDegradation(summary=note, detail=note) for note in spine.degradations
    ]
    prepared: dict[int, _GridPrep] = {}
    pending_half = 0.0
    last_boundary: GridBoundary | None = None
    run_starts: dict[int, float] = {}

    def sting_for_boundary(
        kind: str, before: GridItem, after: GridItem, *, seconds: float
    ) -> tuple[CardMotion | None, str]:
        """The sting ``kind`` names, loaded for this boundary, or ``None``
        and the reason (no browser, not in the Look, failed to load)."""
        name = sting_name(kind)
        if card_look is None or active_rasterizer is None:
            return None, f"sting {name}: no browser to draw it"
        if sting_template_for(card_look, name) is None:
            return None, f"sting {name} is not in the {card_look.name} Look"
        stage_names = [plan.stage_name for plan in plans]
        shooters_seen = (
            tuple(identities[label] for label in labels_tuple if label in identities) if identities else ()
        )
        motion = sting_motion(
            card_look,
            kind,
            seconds=seconds,
            from_label=_grid_item_label(before, stage_names),
            to_label=_grid_item_label(after, stage_names),
            width=canvas.width,
            height=canvas.height,
            fps=canvas.fps,
            rasterizer=active_rasterizer,
            shooters=shooters_seen,
        )
        if motion is None:
            return None, f"sting {name} failed to load from the {card_look.name} Look"
        return motion, ""

    def note_cut(after: int, reason: str) -> None:
        """A boundary that cannot be built becomes a cut: both neighbours
        keep their full length and the result says why."""
        del live[after]
        items[after] = replace(items[after], tail_cut_seconds=0.0)
        items[after + 1] = replace(items[after + 1], head_cut_seconds=0.0)
        text = f"transition after {items[after].name} failed to render ({reason}); rendered as a cut"
        logger.warning("compare grid: %s", text)
        transition_notes.append(OverlayDegradation(summary=text, detail=text))

    def overlay_for(
        plan: GridStagePlan, *, source: GridStagePlan, head_pad: float | None, list_suffix: str
    ) -> StageOverlayPlan | None:
        """The stage's overlay plan for one plan variant: the original
        (``head_pad`` None) takes the render's head pad, byte-identical to
        the no-transition render; a narrowed or edge plan takes its own
        head pad (negative for a tail edge) and the source's tile presence,
        so the clocks and sprite states move with the cut and a tile the
        window has no footage of still shows its figures."""
        free_cells = _unreached_cells(plan)
        race_at = free_cells[0] if race and free_cells and active_rasterizer is not None else None
        if not ((overlay or race_at is not None) and font_path is not None):
            return None
        stage_overlay = _stage_overlay_plan(
            plan if head_pad is None else overlay_plan_for(plan, source=source),
            canvas,
            overlay_data,
            theme_name=overlay_theme,
            font_path=font_path,
            head_pad_seconds=head_pad_seconds if head_pad is None else head_pad,
            work=work,
            rasterizer=active_rasterizer,
            tiles=overlay,
            race_at=race_at,
            list_suffix=list_suffix,
        )
        if not draw_clock:
            # Dropping the clocks is what removes ``drawtext`` from the
            # command: ``_clock_filters`` emits one filter per clock and,
            # with none, emits nothing and hands its own input label
            # straight back, so the rest of the video chain composes onto
            # ``[ovlgrid]`` unchanged.
            stage_overlay = replace(stage_overlay, clocks=())
        return stage_overlay

    def free_still_for(plan: GridStagePlan) -> Path | None:
        free_cells = _unreached_cells(plan)
        race_at = free_cells[0] if race and free_cells and active_rasterizer is not None else None
        if race_at is not None and free_theme is not None:
            cell_w, cell_h = _cell_size(canvas, plan)
            still = work / f"free-stage{plan.stage_number}.png"
            surface_still(width=cell_w, height=cell_h, theme=free_theme).save(still)
            return still
        if (
            free_requested
            and free_cell != "race"
            and free_cells
            and active_rasterizer is not None
            and free_theme is not None
        ):
            return _free_cell_still(
                plan,
                kind=free_cell,
                canvas=canvas,
                theme=free_theme,
                rasterizer=active_rasterizer,
                work=work,
                match_name=match_name,
                match_date=match_date,
                tiles={
                    label: data for (label, number), data in free_tiles.items() if number == plan.stage_number
                },
                expected_rounds=free_rounds.get(plan.stage_number),
            )
        return None

    def match_summary_assets() -> _CardAssets | None:
        """The match summary's still, at the composed size of the last
        stage (every plan shares the grid: the stream layout check above).
        ``None`` (logged) when it could not be saved: the card is skipped."""
        from .overlay_summary import (
            build_match_summary_grid_still,
            extract_match_summary_freezes,
            grid_match_summaries,
        )

        plan = plans[-1]
        title = match_name or (title_page.text if title_page else closing.text if closing else "")
        summaries = grid_match_summaries(
            plans,
            overlay_data or load_overlay_data(shooters),
            title=title,
            duration_seconds=match_summary_seconds,
        )
        freezes = extract_match_summary_freezes(
            plans, work_dir=work / "match-summary", ffmpeg_binary=binary, runner=still_runner
        )
        composed_w, composed_h = _composed_size(canvas, plan)
        still = build_match_summary_grid_still(
            plan,
            summaries,
            freezes,
            width=composed_w,
            height=composed_h,
            title=title,
            theme=load_theme(overlay_theme),
            rasterizer=active_rasterizer,
            accents={
                label: ident.accent for label, ident in (identities or {}).items() if ident.accent is not None
            },
        )
        png = work / "match_summary.png"
        try:
            still.save(png)
        except OSError as exc:
            logger.warning("compare grid: the match summary could not be saved and is skipped: %s", exc)
            return None
        return _CardAssets(png=png)

    def prepare(item: GridItem) -> _GridPrep:
        """Everything an item's encodes read from disk, made once: a card's
        files, or a stage's lower third, hold still and free-cell still. A
        hold still that cannot be composed fails the stage (``failed``),
        as it always did; a card that cannot be drawn is ``skipped``."""
        if isinstance(item, GridCardItem) and item.kind == "match_summary":
            return _GridPrep(assets=match_summary_assets())
        if isinstance(item, GridCardItem):
            plan = plans[item.stage_index if item.stage_index is not None else 0]
            card: MatchTitle | TitleCard
            if item.kind == "slate":
                card = stage_card(
                    plan,
                    style="slate",
                    seconds=title_duration_seconds,
                    expected_rounds=expected_rounds.get(plan.stage_number),
                    variant=card_variant,
                )
            else:
                assert item.card is not None
                card = item.card
            assert item.kind != "match_summary"
            assets = _card_assets(
                card,
                name=item.name,
                slot=item.kind,
                plan=plan,
                backdrop_at="tail" if item.kind == "closing" else "head",
                canvas=canvas,
                look=card_look,
                rasterizer=active_rasterizer,
                work=work,
                ffmpeg_binary=binary,
                still_runner=still_runner,
                identities=identities,
            )
            return _GridPrep(assets=assets)
        plan = item.plan
        prep = _GridPrep()
        if stage_titles == "lower-third" and active_rasterizer is not None and card_look is not None:
            card = stage_card(
                plan,
                style=stage_titles,
                seconds=title_duration_seconds,
                expected_rounds=expected_rounds.get(plan.stage_number),
                variant=card_variant,
            )
            composed_w, composed_h = _composed_size(canvas, plan)
            lt_motion = card_motion(
                card,
                slot="lower_third",
                width=composed_w,
                height=composed_h,
                fps=canvas.fps,
                look=card_look,
                rasterizer=active_rasterizer,
                max_seconds=title_duration_seconds,
                shooters=_tile_identities(plan, identities),
            )
            if lt_motion is not None and not lt_motion.animated:
                image = first_frame_image(lt_motion)
                if image is not None:
                    png = work / f"lower-third-stage{plan.stage_number}.png"
                    image.save(png)
                    prep.lower_third = LowerThirdInput(path=png, seconds=title_duration_seconds)
            elif lt_motion is not None:
                clip_path = work / f"lower-third-stage{plan.stage_number}_motion.mov"
                try:
                    write_motion_clip(lt_motion.frames, out=clip_path, fps=canvas.fps, ffmpeg_binary=binary)
                    prep.lower_third = LowerThirdInput(
                        path=clip_path, seconds=title_duration_seconds, clip=True
                    )
                except MotionClipError as exc:
                    logger.warning(
                        "compare grid: stage %d lower third could not be drawn and is dropped: %s",
                        plan.stage_number,
                        exc,
                    )
                finally:
                    lt_motion.close()
        # ``font_path`` is set exactly when the sprites are; naming both
        # keeps that obvious rather than asserting it.
        free_cells = _unreached_cells(plan)
        race_at = free_cells[0] if race and free_cells and active_rasterizer is not None else None
        prep.has_overlay = (overlay or race_at is not None) and font_path is not None
        if prep.has_overlay and plan.hold_seconds > 0:
            # A missing ``drawtext`` costs the summary nothing: the clock
            # is a separate ffmpeg filter-graph chain (see
            # ``_clock_filters``) that no longer draws once the action
            # ends, and the summary's own text is composed through
            # headless Chromium rasterizing CSS (``overlay_html`` /
            # ``overlay_raster``, issue #683's amendment -- neither PIL nor
            # drawtext), so a host that lost the clock still gets full
            # summaries. A missing rasterizer (see the preflight above)
            # costs the summary its text but not the still itself --
            # ``build_hold_still`` composes the blurred freeze either way.
            #
            # Caught, not raised, and for the same reason a failed ffmpeg
            # call below is: one bad stage is reported and skipped so the
            # rest still stitch. ``overlay_summary`` already degrades an
            # unreadable trim or a bad freeze to a black cell, so what
            # reaches here is the whole-stage kind -- a font that will not
            # load, a disk that will not take the PNG. Building the segment
            # anyway is not an option: without the still the stage's audio
            # would outlast its video, which is the one fault nothing
            # downstream reports.
            try:
                prep.hold_still = _stage_hold_still(
                    plan,
                    canvas,
                    overlay_data,
                    theme_name=overlay_theme,
                    work=work,
                    ffmpeg_binary=binary,
                    runner=still_runner,
                    rasterizer=active_rasterizer,
                    identities=identities,
                )
            except Exception as exc:  # noqa: BLE001 -- one bad stage must not lose the match
                prep.failed = f"could not compose the stage summary still: {exc}"
                logger.warning("compare grid stage %d: %s", plan.stage_number, prep.failed)
                return prep
        prep.free_still = free_still_for(plan)
        return prep

    def stage_command(
        item: GridStageItem,
        prep: _GridPrep,
        plan: GridStagePlan,
        *,
        lower_third: LowerThirdInput | None,
        output: Path,
        head_pad: float | None,
        list_suffix: str,
    ) -> tuple[tuple[str, ...], tuple[Path, ...]]:
        """The stage's command and the files it reads that are not argv
        tokens (:func:`_overlay_inputs`)."""
        stage_overlay = (
            overlay_for(plan, source=item.plan, head_pad=head_pad, list_suffix=list_suffix)
            if prep.has_overlay
            else None
        )
        cmd = build_stage_command(
            plan,
            canvas=canvas,
            output_path=output,
            ffmpeg_binary=binary,
            overlay=stage_overlay,
            hold_still_path=prep.hold_still if plan.hold_seconds > 0 else None,
            lower_third=lower_third,
            inset=inset,
            free_cell_still=prep.free_still,
        )
        return cmd, _overlay_inputs(stage_overlay)

    def encode_edge(item: GridItem, prep: _GridPrep, *, half: float, end: Literal["tail", "head"]) -> Path:
        """A boundary's edge of ``item`` (issue #1244): ``2 * half`` seconds
        around the cut (less where the trims hold no handle), rendered
        with the item's own builder and the grid's N+1 tracks, on the
        boundary runner. Raises when the item cannot provide one."""
        name = f"edge-{item.name}-{end}"
        out = work / f"{name}{SEGMENT_SUFFIX}"
        if isinstance(item, GridCardItem):
            if prep.assets is None:
                raise _EdgeFailedError(f"{item.name} was skipped")
            segment = _encode_card(
                prep.assets,
                seconds=2 * half,
                name=name,
                plan=plans[item.stage_index if item.stage_index is not None else 0],
                canvas=canvas,
                ffmpeg_binary=binary,
                runner=boundary_runner,
                clip_delay_seconds=half if end == "head" else 0.0,
                clip_offset_seconds=item.card_seconds - half if end == "tail" else 0.0,
                encoder=encoder,
            )
            if segment is None:
                raise _EdgeFailedError(f"{name} failed to encode")
            return segment
        if prep.failed is not None:
            raise _EdgeFailedError(prep.failed)
        plan = item.plan
        extra: tuple[Path, ...] = ()
        if end == "tail" and grid_edge_is_hold_only(plan, half=half):
            # The whole edge lies in the hold: a still of the hold PNG, the
            # same N+1 silent tracks a card segment carries.
            if prep.hold_still is None:
                raise _EdgeFailedError("the stage holds its summary but has no still to hold")
            cmd = build_card_segment_command(
                prep.hold_still,
                seconds=2 * half,
                canvas=canvas,
                shooter_labels=labels_tuple,
                output_path=out,
                ffmpeg_binary=binary,
            )
        else:
            edge_plan = grid_edge_plan(plan, half=half, end=end)
            handle = grid_edge_handle(plan, half=half, end=end)
            lower_third = prep.lower_third
            if lower_third is not None:
                if end == "head":
                    # The edge starts ``handle`` before the stage (the boundary
                    # prepends the rest of the half), so the card opens then.
                    lower_third = replace(lower_third, delay_seconds=handle)
                else:
                    skip = plan.duration_seconds - (half - plan.hold_seconds)
                    lower_third = (
                        replace(lower_third, skip_seconds=skip) if skip < lower_third.seconds else None
                    )
            edge_head_pad = (
                head_pad_of(plan) + handle
                if end == "head"
                else head_pad_of(plan) - (plan.duration_seconds - (half - plan.hold_seconds))
            )
            cmd, extra = stage_command(
                item,
                prep,
                edge_plan,
                lower_third=lower_third,
                output=out,
                head_pad=edge_head_pad,
                list_suffix=f"-{end}",
            )
        edge, error = encoder.encode(cmd, out, runner=boundary_runner, extra_inputs=extra)
        if edge is None:
            raise _EdgeFailedError(error)
        return edge

    try:
        for i, item in enumerate(items):
            prep = prepared.pop(i) if i in prepared else prepare(item)
            boundary = live.get(i)
            boundary_segment: Path | None = None
            if boundary is not None:
                nxt = items[i + 1]
                if i + 1 not in prepared:
                    prepared[i + 1] = prepare(nxt)
                half = boundary.duration_seconds / 2.0
                kind = boundary.kind
                sting: CardMotion | None = None
                sting_clip: Path | None = None
                if is_sting(kind):
                    # Issue #1245: the sting's template, or the fade it
                    # rides with a note saying why.
                    sting, reason = sting_for_boundary(kind, item, nxt, seconds=boundary.duration_seconds)
                    if sting is None:
                        text = f"{reason}; transition after {item.name} rendered as a fade"
                        logger.warning("compare grid: %s", text)
                        transition_notes.append(OverlayDegradation(summary=text, detail=text))
                        kind = "fade"
                    else:
                        sting_clip = work / f"{boundary.name}_sting.mov"
                try:
                    tail_edge = encode_edge(item, prep, half=half, end="tail")
                    head_edge = encode_edge(nxt, prepared[i + 1], half=half, end="head")
                    if sting is not None and sting_clip is not None:
                        write_motion_clip(sting.frames, out=sting_clip, fps=canvas.fps, ffmpeg_binary=binary)
                    boundary_out = work / f"{boundary.name}{SEGMENT_SUFFIX}"
                    cmd = build_boundary_segment_command(
                        tail_edge,
                        head_edge,
                        kind=kind,
                        seconds=boundary.duration_seconds,
                        canvas=canvas,
                        shooter_labels=labels_tuple,
                        output_path=boundary_out,
                        ffmpeg_binary=binary,
                        tail_pad_seconds=_grid_missing_handle(item, half=half, end="tail"),
                        head_pad_seconds=_grid_missing_handle(nxt, half=half, end="head"),
                        sting_clip=sting_clip,
                    )
                    edge_keys = (
                        {
                            str(tail_edge): encoder.keys_by_path[tail_edge],
                            str(head_edge): encoder.keys_by_path[head_edge],
                        }
                        if segment_cache is not None
                        else None
                    )
                    boundary_segment, error = encoder.encode(
                        cmd, boundary_out, runner=boundary_runner, virtual_inputs=edge_keys
                    )
                    if boundary_segment is None:
                        raise _EdgeFailedError(error)
                except (_EdgeFailedError, MotionClipError) as exc:
                    note_cut(i, str(exc))
                    item = items[i]
                    boundary = None
                finally:
                    if sting is not None:
                        sting.close()
            # The chapter sits where the cut was: the boundary before this
            # run is already in ``elapsed`` and half of it belongs to the
            # previous item.
            if isinstance(item, GridCardItem) and item.kind == "slate" and item.stage_index is not None:
                run_starts[item.stage_index] = elapsed - pending_half
            segment: Path | None
            if isinstance(item, GridCardItem):
                if prep.assets is None:
                    segment = None
                else:
                    segment = _encode_card(
                        prep.assets,
                        seconds=item.duration_seconds,
                        name=item.name,
                        plan=plans[item.stage_index if item.stage_index is not None else 0],
                        canvas=canvas,
                        ffmpeg_binary=binary,
                        runner=card_runner,
                        clip_offset_seconds=item.head_cut_seconds,
                        encoder=encoder,
                    )
            else:
                stage_start = run_starts.pop(item.index, elapsed - pending_half)
                if prep.failed is not None:
                    outcomes.append(
                        StageOutcome(
                            stage_number=item.plan.stage_number,
                            stage_name=item.plan.stage_name,
                            ok=False,
                            error=prep.failed,
                        )
                    )
                    segment = None
                else:
                    cut = bool(item.head_cut_seconds or item.tail_cut_seconds)
                    plan = (
                        narrow_grid_plan(
                            item.plan, head_cut=item.head_cut_seconds, tail_cut=item.tail_cut_seconds
                        )
                        if cut
                        else item.plan
                    )
                    lower_third = prep.lower_third
                    if lower_third is not None and item.head_cut_seconds:
                        lower_third = (
                            None
                            if item.head_cut_seconds >= lower_third.seconds
                            else replace(lower_third, skip_seconds=item.head_cut_seconds)
                        )
                    cmd, extra = stage_command(
                        item,
                        prep,
                        plan,
                        lower_third=lower_third,
                        output=work / f"{item.name}{SEGMENT_SUFFIX}",
                        head_pad=head_pad_of(item.plan) - item.head_cut_seconds if cut else None,
                        list_suffix="-cut" if cut else "",
                    )
                    segment, error = encoder.encode(
                        cmd,
                        work / f"{item.name}{SEGMENT_SUFFIX}",
                        runner=runner,
                        extra_inputs=extra,
                        report=lambda status, n=item.index, p=item.plan: report(n, p, status),
                    )
                    if segment is None:
                        outcomes.append(
                            StageOutcome(
                                stage_number=item.plan.stage_number,
                                stage_name=item.plan.stage_name,
                                ok=False,
                                error=error,
                            )
                        )
                    else:
                        chapters.append(
                            Chapter(
                                start_seconds=stage_start,
                                title=item.plan.stage_name or f"Stage {item.plan.stage_number}",
                            )
                        )
                        outcomes.append(
                            StageOutcome(
                                stage_number=item.plan.stage_number, stage_name=item.plan.stage_name, ok=True
                            )
                        )
            pending_half = 0.0
            if segment is None:
                # The item is gone; a boundary on either side of it must not
                # stay: the one into it is already in the list and counted,
                # the one out of it has its neighbour trimmed.
                if boundary is not None:
                    note_cut(i, f"{item.name} was not rendered")
                if last_boundary is not None and segments:
                    segments.pop()
                    elapsed -= last_boundary.duration_seconds
                    note = (
                        f"transition into {item.name} dropped: the item was not rendered, and "
                        f"{items[i - 1].name}'s last {last_boundary.duration_seconds / 2.0:g}s went with it"
                    )
                    logger.warning("compare grid: %s", note)
                    transition_notes.append(OverlayDegradation(summary=note, detail=note))
                last_boundary = None
                continue
            segments.append(segment)
            elapsed += item.duration_seconds
            last_boundary = None
            if boundary_segment is not None and boundary is not None:
                segments.append(boundary_segment)
                elapsed += boundary.duration_seconds
                pending_half = boundary.duration_seconds / 2.0
                last_boundary = boundary
    finally:
        # Closed as soon as the stage loop is done, not held open through
        # the final concat/stitch below -- the stitch is ffmpeg-only and
        # never touches the rasterizer. A caller-supplied ``rasterizer``
        # is left alone: its lifecycle was never this function's to manage.
        if owned_rasterizer is not None:
            owned_rasterizer.__exit__(None, None, None)

    if not any(outcome.ok for outcome in outcomes):
        # Cards alone are not a match: a title page over no stages would
        # stitch, exit 0 and hand back a video of nothing.
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
    report(len(plans), None, "stitching")
    completed = _run_ffmpeg(concat_cmd, runner=runner)
    if completed.returncode != 0:
        raise GridRenderError(f"concat stitch failed: {_stderr_text(completed)}")
    if segment_cache is not None:
        segment_cache.evict(keep=encoder.used_keys)

    if chapters and chapters[0].start_seconds > 0.0:
        chapters.insert(0, Chapter(start_seconds=0.0, title=match_title or "Intro"))
    return GridRenderResult(
        output_path=output_path,
        stages=tuple(outcomes),
        degradations=(*degradations, *transition_notes),
        chapters=tuple(chapters),
    )


__all__ = [
    "DEFAULT_INSET_SCALE",
    "GridInset",
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
    "GridProgress",
    "GridRenderError",
    "GridRenderResult",
    "GridRenderStep",
    "GridStagePlan",
    "GridTile",
    "LowerThirdInput",
    "NoticeHook",
    "OverlayDegradation",
    "StageOutcome",
    "StageOverlayPlan",
    "TileClock",
    "audio_track_labels",
    "build_card_segment_command",
    "build_concat_command",
    "build_stage_command",
    "build_stage_plans",
    "derive_frame_rate",
    "render_grid_mp4",
    "stage_card",
]
