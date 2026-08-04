"""Direct-to-MP4 renderer for multi-shooter compare grids.

Sits beside :mod:`splitsmith.compare.emitter` (which emits FCPXML) and
consumes the same ``project_loader`` bundles and ``layout`` grid math.
Renders one ffmpeg call per stage -- scale + pad each tile to a uniform
cell, ``xstack`` them into the grid, map every shooter's audio as its
own output track -- then stitches the per-stage temps with the
``concat`` demuxer at ``-c copy``.

Phase 0 scope: no overlay, no transitions, no title cards. The overlay
lands in phase 1 as pre-rendered sprite PNGs; nothing here should make
that harder.

Determinism / testability: command construction is split into pure
functions (:func:`build_stage_command` / :func:`build_concat_command`)
with an injectable runner, mirroring :mod:`splitsmith.mp4_render` and
:mod:`splitsmith.trim`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..runtime import runtime
from .layout import Layout2Up, choose_grid, grid_shape
from .project_loader import CompareShooterBundle

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_CANVAS_WIDTH = 3840
DEFAULT_CANVAS_HEIGHT = 2160

#: Last resort only. The render's frame rate follows the audio-source
#: shooter's footage (see :func:`derive_frame_rate`); this is what a
#: canvas reports when nobody pinned a rate and there is no bundle to
#: derive one from.
FALLBACK_FRAME_RATE_NUM = 30000
FALLBACK_FRAME_RATE_DEN = 1001


class GridRenderError(RuntimeError):
    """ffmpeg refused to render a grid stage or the final stitch."""


@dataclass(frozen=True)
class GridCanvas:
    """Output geometry for the whole render.

    Pinned once and applied to every stage: ``concat -c copy`` rejects
    segments whose video parameters differ.

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

    row: int
    col: int


@dataclass(frozen=True)
class GridStagePlan:
    """Everything one ffmpeg invocation needs for one stage."""

    stage_number: int
    stage_name: str
    tiles: tuple[GridTile, ...]
    duration_seconds: float
    audio_label: str
    rows: int
    cols: int


def build_stage_plans(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    head_pad_seconds: float,
    tail_pad_seconds: float,
    layout_2up: Layout2Up = "horizontal",
) -> tuple[GridStagePlan, ...]:
    """Plan one grid stage per stage number present on any shooter.

    Slots are alphabetical by label and stable across stages, matching
    ``compare/emitter.py``'s rule: a label always lands in the same cell
    and a missing trim becomes filler rather than reshuffling the grid.
    Stage names follow the emitter too: the audio-source shooter's
    spelling wins, so the FCPXML and MP4 exports of one match cannot
    label the same stage differently.
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

    if audio_label not in labels:
        raise ValueError(f"audio_label={audio_label!r} matches no shooter. Labels: {', '.join(labels)}")

    by_label = {s.label: s for s in shooters}
    audio_bundle = by_label[audio_label]
    # Task 2 unmutes only the audio tile. A shooter with no stages at all
    # is a filler everywhere, so the render would come out silent with
    # nothing to explain it. Missing a single stage is different, and
    # fine: that one stage just has no unmuted tile.
    if not audio_bundle.stages_by_number:
        raise ValueError(
            f"audio_label={audio_label!r} has no stages with trims; the render would have no audio"
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
    ``concat -c copy`` refuses segments whose frame rate differs; the
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


def build_stage_command(
    plan: GridStagePlan,
    *,
    canvas: GridCanvas,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """Build the ffmpeg invocation rendering one grid stage.

    Stream layout is fixed at one video plus one audio track per tile,
    in alphabetical label order, regardless of which shooters actually
    have a trim for this stage. ``concat -c copy`` rejects segments
    whose stream layout differs, so a missing tile contributes a black
    ``color`` source and a silent ``anullsrc`` track rather than
    nothing at all.

    Cells the roster does not reach (see :func:`_unreached_cells`) get a
    black ``color`` source too, but *video only*: an empty cell is not a
    shooter, and giving it a track would take the audio count away from
    the roster size -- the very thing the paragraph above pins.
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

    args += [
        "-filter_complex",
        _build_filter_graph(plan, canvas, video_index, audio_index, empty_index),
    ]

    args += ["-map", "[final]"]
    for slot in range(len(plan.tiles)):
        args += ["-map", f"[a{slot}]"]

    # Only the audio-source shooter plays by default. Marking every track
    # default leaves the player free to pick, so the grid can come out
    # with the wrong shooter's audio. ``build_stage_plans`` guarantees the
    # label is on every plan; name it if a hand-built plan disagrees,
    # rather than letting ``next`` raise a bare StopIteration.
    default_slot = next(
        (i for i, t in enumerate(plan.tiles) if t.label == plan.audio_label),
        None,
    )
    if default_slot is None:
        raise ValueError(
            f"audio_label={plan.audio_label!r} matches no tile in stage {plan.stage_number}; "
            f"tiles: {', '.join(t.label for t in plan.tiles)}"
        )
    args += list(_disposition_args([t.label for t in plan.tiles], default_slot))
    args += list(_track_naming_args([t.label for t in plan.tiles]))

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
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return tuple(args)


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
    """
    cell_w, cell_h = _cell_size(canvas, plan)
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
        # before its audio on every filler-free stage. ``concat -c copy``
        # then carries that gap into every later stage. Padding by a full
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
    parts.append("[grid]format=yuv420p[final]")

    for slot, tile in enumerate(plan.tiles):
        # ``aresample=async=1`` keeps a track that starts short from
        # drifting; ``apad`` + ``atrim`` guarantee every track is exactly
        # the stage duration so the segment's streams end together.
        # ``adelay`` mirrors the video's ``tpad`` so a lead-padded tile's
        # audio stays locked to its picture.
        # ``aformat`` is the audio half of the concat invariant: a mono
        # trim and the stereo ``anullsrc`` filler would otherwise put
        # differently-shaped tracks in the same slot across segments.
        delay_ms = int(round(tile.lead_pad_seconds * 1000))
        lead = f"adelay={delay_ms}:all=1," if delay_ms > 0 else ""
        parts.append(
            f"[{audio_index[slot]}:a]asetpts=PTS-STARTPTS,{lead}aresample=async=1,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad,atrim=0:{plan.duration_seconds:g}[a{slot}]"
        )

    return ";".join(parts)


def build_concat_command(
    *,
    list_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    audio_labels: Sequence[str] = (),
    default_audio_label: str | None = None,
) -> tuple[str, ...]:
    """Stitch the per-stage temps without re-encoding.

    ``-map 0`` is load-bearing, not decoration. Without it ffmpeg's
    default stream selection keeps one stream per type, so a stitch of
    four-shooter segments comes out with a single audio track and the
    per-shooter audio the whole feature exists for is gone -- silently,
    at the very last step, after every stage has been encoded (verified
    against ffmpeg 7.0.2).

    ``audio_labels`` / ``default_audio_label`` restore the track names
    and the default flag on the stitched file. Stream copy does not
    carry them across the concat demuxer: the muxer re-derives the
    default flag and lands it on the first audio track, which would
    play the alphabetically-first shooter instead of the audio source.
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
        "-c",
        "copy",
    ]
    args += list(_track_naming_args(audio_labels))
    if default_audio_label is not None:
        if default_audio_label not in audio_labels:
            raise ValueError(
                f"default_audio_label={default_audio_label!r} is not in audio_labels: "
                f"{', '.join(audio_labels) or '(none given)'}"
            )
        args += list(_disposition_args(audio_labels, list(audio_labels).index(default_audio_label)))
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
    """Result of a whole grid render, including partial failures."""

    output_path: Path
    stages: tuple[StageOutcome, ...]

    @property
    def failed(self) -> tuple[StageOutcome, ...]:
        return tuple(s for s in self.stages if not s.ok)


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
    ffmpeg_binary: str | None = None,
    runner: Runner = subprocess.run,
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

    ``work_dir`` holds the per-stage segments and the concat list. It
    defaults to a directory beside the output -- same filesystem, so a
    match's worth of 4K segments doesn't have to fit in ``/tmp`` -- and
    is *not* cleaned up: the segments are what a failed stitch is
    debugged from, and skipping cleanup keeps a successful render from
    deleting a caller's own directory. Callers that want it gone should
    pass a path they own.
    """
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
    )
    if not plans:
        raise GridRenderError("no stages to render -- no shooter has an exported trim")

    # ``concat -c copy`` rejects segments whose stream layout differs, and
    # skipping a failed stage means the stitch list is not simply "all of
    # them". Every plan from ``build_stage_plans`` carries one tile per
    # label, so this holds today; check it anyway rather than discover a
    # future planner change at the stitch, after the whole match has been
    # encoded.
    labels = tuple(tile.label for tile in plans[0].tiles)
    for plan in plans[1:]:
        other = tuple(tile.label for tile in plan.tiles)
        if other != labels:
            raise GridRenderError(
                f"stage {plan.stage_number} has a different stream layout to stage "
                f"{plans[0].stage_number} ({', '.join(other)} vs {', '.join(labels)}); "
                "concat -c copy cannot stitch those together"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    work = work_dir or output_path.parent / ".compare-grid-work"
    work.mkdir(parents=True, exist_ok=True)

    outcomes: list[StageOutcome] = []
    segments: list[Path] = []
    for plan in plans:
        segment = work / f"stage{plan.stage_number}.mp4"
        cmd = build_stage_command(plan, canvas=canvas, output_path=segment, ffmpeg_binary=binary)
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
    # copy does not carry them across the concat demuxer, and the muxer's
    # own default lands on the first audio track -- the alphabetically
    # first shooter, not the one the caller chose.
    concat_cmd = build_concat_command(
        list_path=list_path,
        output_path=output_path,
        ffmpeg_binary=binary,
        audio_labels=labels,
        default_audio_label=plans[0].audio_label,
    )
    completed = _run_ffmpeg(concat_cmd, runner=runner)
    if completed.returncode != 0:
        raise GridRenderError(f"concat stitch failed: {_stderr_text(completed)}")

    return GridRenderResult(output_path=output_path, stages=tuple(outcomes))


__all__ = [
    "DEFAULT_CANVAS_HEIGHT",
    "DEFAULT_CANVAS_WIDTH",
    "FALLBACK_FRAME_RATE_DEN",
    "FALLBACK_FRAME_RATE_NUM",
    "GridCanvas",
    "GridRenderError",
    "GridRenderResult",
    "GridStagePlan",
    "GridTile",
    "StageOutcome",
    "build_concat_command",
    "build_stage_command",
    "build_stage_plans",
    "derive_frame_rate",
    "render_grid_mp4",
]
