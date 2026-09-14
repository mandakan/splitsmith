"""MP4 renderer for the Composition IR via ffmpeg (issue #174).

Walks ``composition.Composition`` and produces a stitched MP4 by:

1. Building one ffmpeg invocation per stage that re-encodes the primary
   with PiP secondaries and the alpha overlay composited in. Each stage
   writes to a temp ``.mp4`` in the work directory.
2. Concatenating the per-stage temps via the ``concat`` demuxer with
   ``-c copy`` so the final stitch doesn't re-encode.

Coverage in this PR matches the FCPXML / FCP7 renderers for the
in-scope features:

- Primary clip per stage on the base layer.
- Secondary cams composited with optional PiP transform (scale +
  position). The IR's pixel-space ``Transform`` (+Y up, sequence-centre
  origin) converts to ffmpeg's ``overlay`` filter convention (+Y down,
  top-left origin).
- Alpha overlay composited on the topmost layer.
- Per-stage trim via ``-ss`` / ``-t`` on the input.

Generated cards and clips (issue #973). ``plan_timeline`` walks the IR
into an ordered spine -- intro, title page, (slate, stage) per stage,
closing, outro -- and every item that is not a stage becomes its own
segment temp encoded with the same ``_encode_args`` at the sequence
size and rate: a still (``-loop 1`` over a PNG plus ``anullsrc`` audio)
for a card, a conforming re-encode for an intro / outro clip. A
lower-third rides the stage's own filter graph instead (an ``overlay``
with an alpha fade-out), so it adds no time. The cards themselves are
composed by :mod:`splitsmith.overlay_card` through an injected
:class:`~splitsmith.overlay_raster.Rasterizer`; no usable browser
degrades -- every card skipped, the degradation recorded on the
result -- rather than failing the render, mirroring
``compare/mp4_grid``'s preflight.

With generated segments present the stitch re-encodes the audio (video
stays a stream copy) so ``anullsrc`` and the trims' audio need not
agree on a sample rate; with none, the argv is what it always was.
Known limit: a primary trim with no audio stream next to a card
segment would break the stitch. Every primary is a camera trim with
audio, so this does not occur in practice.

Out of scope (sibling issues): transitions (#195), audio mix tweaks.
Audio is taken from the primary; secondaries / overlay contribute
video only.

The IR is the contract: ``render_mp4`` is the second non-XML renderer
that consumes it (FCPXML, FCP7 XML, MP4 -- three targets, one IR).

Determinism / testability: command construction is split into pure
functions (``_build_stage_command`` / ``_build_concat_command``) so
unit tests can verify the ffmpeg invocation without shelling out. The
runner is injectable, mirroring the pattern in
``splitsmith.trim``.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .composition import Composition, ConnectedClip, Segment, SequenceFormat, Stage, TitleCard, Transform
from .overlay_card import Card, build_card_still, build_lower_third
from .overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .overlay_theme import ThemeName, load_theme

logger = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]


class FFmpegError(RuntimeError):
    """ffmpeg exited non-zero or could not be invoked."""


@dataclass(frozen=True)
class Mp4RenderResult:
    """What a render wrote.

    ``duration_seconds`` is the length of the stitched timeline as
    actually written -- footage plus every card and clip that made it
    in, so a skipped card is not counted. It is the figure
    ``match_exports.MatchExportResult.duration_seconds`` reports, and
    never a wall clock (see CLAUDE.md on the two ``duration_seconds``
    fields).

    ``degradations`` is what the render did *not* do -- today, only
    "no usable browser, every card skipped". A returned field and not
    only a log line so every caller can put it in front of the user.
    """

    output_path: Path
    duration_seconds: float
    degradations: tuple[str, ...] = ()


def render_mp4(
    composition: Composition,
    *,
    output_path: Path,
    work_dir: Path | None = None,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    youtube_preset: bool = False,
    rasterizer: Rasterizer | None = None,
    overlay_theme: ThemeName = "splitsmith",
) -> Mp4RenderResult:
    """Render ``composition`` as a stitched ``.mp4`` at ``output_path``.

    ``work_dir`` is where per-stage temps live; defaults to a fresh
    ``TemporaryDirectory`` cleaned up on return. Pass an explicit path
    when debugging; the per-stage MP4s are easier to inspect than the
    final concat output.

    ``youtube_preset`` swaps the default per-stage encode for YouTube's
    recommended H.264 profile / GOP / colour tags (issue #204 layer 2).
    The concat step keeps the video a stream copy in either case.

    ``rasterizer`` (issue #973) composes the generated cards. Left
    ``None``, a composition that carries any card gets one
    :class:`~splitsmith.overlay_raster.ChromiumRasterizer` for the whole
    render, preflighted before any encode so a missing browser is found
    in the first second rather than after every stage has rendered. A
    caller who passes their own owns its lifecycle. No usable browser
    degrades: every card is skipped and the loss is recorded on
    :attr:`Mp4RenderResult.degradations`; the render never fails
    because of a card. ``overlay_theme`` picks the card typography.
    """
    plans = [_plan_stage(stage, composition.sequence) for stage in composition.stages]
    if not plans:
        raise ValueError("render_mp4 requires at least one stage")

    # The concat-demuxer pipeline (stream-copy) requires every per-stage
    # temp share the same frame rate as the sequence. The FCPXML / FCP7
    # path lifted this restriction in #233 because their NLE readers
    # conform per-asset rates; the MP4 path can't conform without
    # re-encoding through an ``fps=`` filter, which is a larger
    # change than this issue's scope. Surface a clear error naming the
    # offenders so the user knows whether to switch renderer or
    # convert sources externally.
    seq = composition.sequence
    mismatched: list[str] = []
    for stage in composition.stages:
        meta = stage.primary.metadata
        if meta.frame_rate_num != seq.frame_rate_num or meta.frame_rate_den != seq.frame_rate_den:
            mismatched.append(f"{stage.name} ({meta.frame_rate_num}/{meta.frame_rate_den})")
    if mismatched:
        raise ValueError(
            f"mp4 renderer requires all stages at the timeline frame rate "
            f"({seq.frame_rate_num}/{seq.frame_rate_den}); these stages differ: "
            + ", ".join(mismatched)
            + ". Switch to the FCPXML or FCP7 renderer (which conform "
            "per-asset rates) or convert the sources to a shared rate first."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timeline = plan_timeline(composition, plans=plans)

    # The rasterizer preflight, mirroring ``compare/mp4_grid``'s: opened
    # once for the whole render, before any encode, and only when a card
    # actually needs one. A caller-supplied rasterizer is used as-is.
    degradations: tuple[str, ...] = ()
    active: Rasterizer | None = rasterizer
    owned: ChromiumRasterizer | None = None
    if timeline.needs_rasterizer and rasterizer is None:
        owned = ChromiumRasterizer()
        try:
            active = owned.__enter__()
        except RasterizerUnavailableError as exc:
            owned = None
            active = None
            degradations = (f"cards skipped: {exc.detail}",)
            logger.warning("%s", degradations[0])

    try:
        if work_dir is None:
            with tempfile.TemporaryDirectory(prefix="splitsmith-mp4-") as tmp:
                return _render_with_work_dir(
                    composition,
                    timeline,
                    output_path=output_path,
                    work_dir=Path(tmp),
                    ffmpeg_binary=ffmpeg_binary,
                    runner=runner,
                    youtube_preset=youtube_preset,
                    rasterizer=active,
                    overlay_theme=overlay_theme,
                    degradations=degradations,
                )
        work_dir.mkdir(parents=True, exist_ok=True)
        return _render_with_work_dir(
            composition,
            timeline,
            output_path=output_path,
            work_dir=work_dir,
            ffmpeg_binary=ffmpeg_binary,
            runner=runner,
            youtube_preset=youtube_preset,
            rasterizer=active,
            overlay_theme=overlay_theme,
            degradations=degradations,
        )
    finally:
        if owned is not None:
            owned.__exit__(None, None, None)


def _render_with_work_dir(
    composition: Composition,
    timeline: TimelinePlan,
    *,
    output_path: Path,
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
    youtube_preset: bool,
    rasterizer: Rasterizer | None,
    overlay_theme: ThemeName,
    degradations: tuple[str, ...],
) -> Mp4RenderResult:
    sequence = composition.sequence
    theme = load_theme(overlay_theme) if timeline.needs_rasterizer else None
    segments: list[tuple[Path, float]] = []
    generated = False
    for item in timeline.items:
        if isinstance(item, _StageItem):
            lower_third: _LowerThirdInput | None = None
            if item.lower_third is not None and rasterizer is not None and theme is not None:
                image = build_lower_third(
                    item.lower_third,
                    width=sequence.width,
                    height=sequence.height,
                    theme=theme,
                    rasterizer=rasterizer,
                )
                if image is not None:
                    png = work_dir / f"lower_third_{item.index:03d}.png"
                    image.save(png)
                    lower_third = _LowerThirdInput(path=png, card=item.lower_third)
            stage_out = work_dir / f"stage_{item.index:03d}.mp4"
            cmd = _build_stage_command(
                item.plan,
                sequence=sequence,
                output_path=stage_out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
                lower_third=lower_third,
            )
            _run(cmd, runner=runner)
            segments.append((stage_out, item.duration_seconds))
        elif isinstance(item, _StillItem):
            if rasterizer is None or theme is None:
                continue  # already recorded as a degradation up front
            backdrop = _grab_backdrop(
                item, timeline, work_dir=work_dir, ffmpeg_binary=ffmpeg_binary, runner=runner
            )
            image = build_card_still(
                item.card,
                width=sequence.width,
                height=sequence.height,
                theme=theme,
                rasterizer=rasterizer,
                backdrop=backdrop,
            )
            if image is None:
                continue  # logged by overlay_card; a card is its text
            png = work_dir / f"{item.name}.png"
            image.save(png)
            still_out = work_dir / f"{item.name}.mp4"
            cmd = _build_still_command(
                png,
                seconds=item.duration_seconds,
                sequence=sequence,
                output_path=still_out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
            )
            _run(cmd, runner=runner)
            segments.append((still_out, item.duration_seconds))
            generated = True
        else:
            clip_out = work_dir / f"{item.kind}.mp4"
            cmd = _build_segment_command(
                item.segment,
                sequence=sequence,
                output_path=clip_out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
            )
            _run(cmd, runner=runner)
            segments.append((clip_out, item.duration_seconds))
            generated = True

    list_path = work_dir / "concat.txt"
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p, _ in segments),
        encoding="utf-8",
    )
    cmd = _build_concat_command(
        list_path=list_path,
        output_path=output_path,
        ffmpeg_binary=ffmpeg_binary,
        reencode_audio=generated,
    )
    _run(cmd, runner=runner)
    return Mp4RenderResult(
        output_path=output_path,
        duration_seconds=sum(seconds for _, seconds in segments),
        degradations=degradations,
    )


#: How far before a backdrop's target frame the grab starts reading. A
#: seek straight to the last timestamp can land past the final frame
#: and write nothing (see ``compare/overlay_summary`` on the same trap),
#: so the read starts a window early and ``-update 1`` keeps the last
#: frame decoded.
_BACKDROP_WINDOW_SECONDS = 0.5


def _grab_backdrop(
    item: _StillItem,
    timeline: TimelinePlan,
    *,
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
) -> Path | None:
    """Pull the frame a full-frame card sits on, or ``None`` when there is
    no stage to take it from or the grab produced no file.

    A title page and a slate take the first visible frame of the stage
    they precede; the closing card takes the last visible frame of the
    last stage. A failed grab is not an error: the card composes on the
    theme's flat surface instead.
    """
    if item.backdrop_stage_index is None:
        return None
    stage = timeline.stage(item.backdrop_stage_index)
    if stage is None:
        return None
    plan = stage.plan
    if item.backdrop_at == "head":
        seek = plan.head_trim_seconds
    else:
        seek = max(0.0, plan.head_trim_seconds + plan.effective_seconds - _BACKDROP_WINDOW_SECONDS)
    out = work_dir / f"{item.name}_backdrop.png"
    out.unlink(missing_ok=True)
    cmd = (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-ss",
        f"{seek:g}",
        "-t",
        f"{_BACKDROP_WINDOW_SECONDS:g}",
        "-i",
        str(plan.stage.primary.path),
        "-an",
        "-update",
        "1",
        str(out),
    )
    try:
        _run(cmd, runner=runner)
    except FFmpegError as exc:
        logger.warning("could not grab a backdrop frame for %s (%s); the card composes flat", item.name, exc)
        return None
    try:
        if out.stat().st_size == 0:
            return None
    except OSError:
        return None
    return out


def _run(cmd: tuple[str, ...], *, runner: Runner) -> None:
    try:
        runner(list(cmd), check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg failed (exit {exc.returncode}): "
            f"{(exc.stderr or exc.stdout or '').strip()[-2000:] or '(no output)'}"
        ) from exc


# --- planning -------------------------------------------------------------


@dataclass(frozen=True)
class _StagePlan:
    """Per-stage timing + alignment derived from the IR.

    ``head_trim_seconds`` is how far we ``-ss`` into the primary;
    ``effective_seconds`` is how long the stage runs on the spine.
    ``cam_alignments`` carries each cam's seek-into-source plus the
    spine time the cam should appear, mirroring the head-slip math the
    FCPXML / FCP7 renderers do in frames.
    """

    stage: Stage
    head_trim_seconds: float
    effective_seconds: float
    cam_alignments: tuple[_CamAlignment, ...]


@dataclass(frozen=True)
class _CamAlignment:
    cam: ConnectedClip
    cam_seek_seconds: float  # ``-ss`` value for the cam input
    cam_spine_start: float  # spine time when the cam first appears
    cam_visible_seconds: float  # how long the cam shows on the spine


ItemKind = Literal["intro", "title_page", "slate", "stage", "closing", "outro"]


@dataclass(frozen=True)
class _StageItem:
    """One stage on the spine, with the lower-third that rides its head."""

    index: int
    plan: _StagePlan
    lower_third: TitleCard | None
    kind: ItemKind = "stage"

    @property
    def duration_seconds(self) -> float:
        return self.plan.effective_seconds


@dataclass(frozen=True)
class _StillItem:
    """A generated full-frame card held on the spine for its duration.

    ``backdrop_stage_index`` / ``backdrop_at`` say which stage's frame the
    card sits on: the head of the stage it precedes, or the tail of the
    last stage for the closing card.
    """

    kind: ItemKind
    name: str
    card: Card
    duration_seconds: float
    backdrop_stage_index: int | None
    backdrop_at: Literal["head", "tail"] = "head"


@dataclass(frozen=True)
class _ClipItem:
    """An intro / outro clip, re-encoded to the sequence."""

    kind: ItemKind
    segment: Segment

    @property
    def duration_seconds(self) -> float:
        return self.segment.asset.metadata.duration_seconds


SpineItem = _StageItem | _StillItem | _ClipItem


@dataclass(frozen=True)
class TimelinePlan:
    """The ordered spine and its total length (issue #973).

    Pure: derived from the IR alone, so ``match_exports`` can quote the
    timeline length before anything is encoded. ``duration_seconds``
    counts every item as planned; :attr:`Mp4RenderResult.duration_seconds`
    is the same sum over what was actually written.
    """

    items: tuple[SpineItem, ...]

    @property
    def duration_seconds(self) -> float:
        return sum(item.duration_seconds for item in self.items)

    @property
    def needs_rasterizer(self) -> bool:
        return any(
            isinstance(item, _StillItem) or (isinstance(item, _StageItem) and item.lower_third is not None)
            for item in self.items
        )

    @property
    def has_generated_segments(self) -> bool:
        return any(not isinstance(item, _StageItem) for item in self.items)

    def stage(self, index: int) -> _StageItem | None:
        for item in self.items:
            if isinstance(item, _StageItem) and item.index == index:
                return item
        return None


def plan_timeline(composition: Composition, *, plans: list[_StagePlan] | None = None) -> TimelinePlan:
    """Walk the IR into spine order: intro, title page, then per stage a
    slate (when its title is one) and the stage itself, then the closing
    card and the outro. A lower-third title is attached to its stage
    rather than placed on the spine, since it overlays the head and adds
    no time."""
    stage_plans = (
        plans if plans is not None else [_plan_stage(s, composition.sequence) for s in composition.stages]
    )
    items: list[SpineItem] = []
    if composition.intro is not None:
        items.append(_ClipItem(kind="intro", segment=composition.intro))
    if composition.title_page is not None:
        items.append(
            _StillItem(
                kind="title_page",
                name="title_page",
                card=composition.title_page,
                duration_seconds=composition.title_page.duration_seconds,
                backdrop_stage_index=0,
            )
        )
    for index, (stage, plan) in enumerate(zip(composition.stages, stage_plans, strict=True)):
        title = stage.title
        lower_third: TitleCard | None = None
        if title is not None and title.style == "slate":
            items.append(
                _StillItem(
                    kind="slate",
                    name=f"slate_{index:03d}",
                    card=title,
                    duration_seconds=title.duration_seconds,
                    backdrop_stage_index=index,
                )
            )
        elif title is not None:
            lower_third = title
        items.append(_StageItem(index=index, plan=plan, lower_third=lower_third))
    if composition.closing is not None:
        items.append(
            _StillItem(
                kind="closing",
                name="closing",
                card=composition.closing,
                duration_seconds=composition.closing.duration_seconds,
                backdrop_stage_index=len(composition.stages) - 1,
                backdrop_at="tail",
            )
        )
    if composition.outro is not None:
        items.append(_ClipItem(kind="outro", segment=composition.outro))
    return TimelinePlan(items=tuple(items))


def _plan_stage(stage: Stage, sequence_format) -> _StagePlan:  # type: ignore[no-untyped-def]
    duration = stage.primary.metadata.duration_seconds
    head_avail = max(0.0, stage.beep_offset_seconds)
    if stage.markers:
        last_local = max(m.time_seconds for m in stage.markers)
    else:
        last_local = stage.beep_offset_seconds
    tail_avail = max(0.0, duration - last_local)
    head_trim_seconds = max(0.0, head_avail - stage.head_pad_seconds)
    tail_trim_seconds = max(0.0, tail_avail - stage.tail_pad_seconds)
    effective_seconds = duration - head_trim_seconds - tail_trim_seconds
    if effective_seconds <= 0:
        raise ValueError(
            f"stage {stage.name!r} would have non-positive effective duration "
            f"after trim ({effective_seconds:.3f}s); reduce head/tail pad"
        )

    cam_alignments: list[_CamAlignment] = []
    visible_head = head_trim_seconds  # source time of the visible head in the primary
    for sec in stage.secondaries:
        assert sec.beep_offset_seconds is not None  # cam role
        # Same head-slip math as the FCPXML emitter, expressed in seconds.
        delta = (stage.beep_offset_seconds - visible_head) - sec.beep_offset_seconds
        if delta >= 0:
            seek = 0.0
            spine_start = delta
        else:
            seek = -delta
            spine_start = 0.0
        cam_total = sec.asset.metadata.duration_seconds
        # The cam shows from spine_start until either (a) its own media
        # runs out or (b) the stage ends.
        cam_visible = min(cam_total - seek, effective_seconds - spine_start)
        cam_alignments.append(
            _CamAlignment(
                cam=sec,
                cam_seek_seconds=seek,
                cam_spine_start=spine_start,
                cam_visible_seconds=max(0.0, cam_visible),
            )
        )

    return _StagePlan(
        stage=stage,
        head_trim_seconds=head_trim_seconds,
        effective_seconds=effective_seconds,
        cam_alignments=tuple(cam_alignments),
    )


# --- command construction -------------------------------------------------


@dataclass(frozen=True)
class _LowerThirdInput:
    """A rasterized lower-third PNG and the card that says how long it shows."""

    path: Path
    card: TitleCard


#: How long a lower-third fades out for, at the end of its window.
LOWER_THIRD_FADE_SECONDS = 0.5


def _build_stage_command(
    plan: _StagePlan,
    *,
    sequence,  # type: ignore[no-untyped-def]
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
    lower_third: _LowerThirdInput | None = None,
) -> tuple[str, ...]:
    """Build the ffmpeg invocation that renders one stage to ``output_path``.

    The invocation re-encodes -- there's no general way to bake
    overlays + PiP without re-encoding. For the non-composited subset
    of cases (no cams, no overlay) we still re-encode for simplicity;
    a stream-copy fast-path is a follow-up if encode time becomes a
    problem.

    ``youtube_preset`` swaps the encode params (codec, GOP, colour
    tags, audio bitrate) to YouTube's recommended profile.

    ``lower_third`` (issue #973) adds the rasterized card as one more
    looped image input, composited last -- above the alpha overlay --
    over the stage's head, and fading out over the last
    :data:`LOWER_THIRD_FADE_SECONDS` of the card's own duration.
    """
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    stage = plan.stage

    # Primary input: trim with ``-ss``/``-t`` before ``-i`` for fast
    # seeking. The buffer in the trimmed clip absorbs any seek
    # imprecision; same trade-off as ``trim.py`` makes.
    args += [
        "-ss",
        f"{plan.head_trim_seconds:g}",
        "-t",
        f"{plan.effective_seconds:g}",
        "-i",
        str(stage.primary.path),
    ]

    # Secondary cam inputs.
    for align in plan.cam_alignments:
        args += [
            "-ss",
            f"{align.cam_seek_seconds:g}",
            "-t",
            f"{align.cam_visible_seconds:g}",
            "-i",
            str(align.cam.asset.path),
        ]

    # Overlay input (if any). Same head-trim window as the primary --
    # the overlay was rendered to mirror the primary frame-for-frame.
    overlay_index: int | None = None
    if stage.overlay is not None:
        overlay_index = 1 + len(plan.cam_alignments)
        args += [
            "-ss",
            f"{plan.head_trim_seconds:g}",
            "-t",
            f"{plan.effective_seconds:g}",
            "-i",
            str(stage.overlay.asset.path),
        ]

    lower_third_graph: tuple[int, float] | None = None
    if lower_third is not None:
        lower_third_index = 1 + len(plan.cam_alignments) + (1 if overlay_index is not None else 0)
        lower_third_graph = (lower_third_index, lower_third.card.duration_seconds)
        args += [
            "-loop",
            "1",
            "-framerate",
            _rate_string(sequence),
            "-t",
            f"{lower_third.card.duration_seconds:g}",
            "-i",
            str(lower_third.path),
        ]

    filter_graph = _build_stage_filter_graph(
        plan,
        sequence=sequence,
        overlay_input_index=overlay_index,
        lower_third=(lower_third_index, lower_third.card.duration_seconds)
        if lower_third is not None
        else None,
    )

    args += [
        "-filter_complex",
        filter_graph,
        "-map",
        "[final]",
        "-map",
        "0:a?",  # primary audio when present, no error if absent
    ]
    args += list(_encode_args(sequence, youtube_preset=youtube_preset))
    args += [str(output_path)]
    return tuple(args)


def _encode_args(
    sequence,  # type: ignore[no-untyped-def]
    *,
    youtube_preset: bool,
) -> tuple[str, ...]:
    """Return the ``-c:v`` ... ``-movflags +faststart`` slice of the
    ffmpeg invocation. The default profile keeps today's lossy-but-fast
    encode (CRF 20, AAC 192k); ``youtube_preset`` swaps in YouTube's
    recommended params (issue #204 layer 2).

    Resolution / fps inform GOP only -- 2 seconds at the sequence frame
    rate, rounded to the nearest frame. Quality is CRF 18 universally;
    YouTube re-encodes regardless, so a single-pass quality target is
    enough and avoids the extra runner invocation a true two-pass
    encode would require.
    """
    if not youtube_preset:
        return (
            "-c:v",
            "libx264",
            "-preset",
            "fast",
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
        )
    fps = sequence.frame_rate_num / max(1, sequence.frame_rate_den)
    gop = max(1, int(round(fps * 2)))
    return (
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",  # closed GOP -- no scene-cut keyframes between the fixed boundaries
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-c:a",
        "aac",
        "-b:a",
        "384k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    )


def _rate_string(sequence: SequenceFormat) -> str:
    return f"{sequence.frame_rate_num}/{sequence.frame_rate_den}"


def _build_stage_filter_graph(
    plan: _StagePlan,
    *,
    sequence,  # type: ignore[no-untyped-def]
    overlay_input_index: int | None,
    lower_third: tuple[int, float] | None = None,
) -> str:
    """Compose primary + cams + overlay into a single ``-filter_complex``.

    Primary becomes ``[base]``; each cam is scaled (when it has a
    transform) and overlaid at its computed corner with an ``enable``
    expression so cams that appear late on the spine don't show until
    their start. The overlay -- when present -- composites last so it
    sits on top of all cams.
    """
    parts: list[str] = []
    parts.append("[0:v]setpts=PTS-STARTPTS,format=yuv420p[base]")

    base_label = "base"
    for cam_idx, align in enumerate(plan.cam_alignments):
        input_label = f"{1 + cam_idx}:v"
        cam_label = f"cam{cam_idx}"
        scaled_label = f"{cam_label}_scaled"
        transform = align.cam.transform

        # Each cam gets setpts-zeroed and scaled when needed.
        scale_chain = "setpts=PTS-STARTPTS"
        if transform is not None and transform.scale != 1.0:
            target_w = int(round(sequence.width * transform.scale))
            target_h = int(round(sequence.height * transform.scale))
            scale_chain += f",scale={target_w}:{target_h}"
        parts.append(f"[{input_label}]{scale_chain}[{scaled_label}]")

        # ffmpeg's overlay filter places the secondary at top-left
        # (X, Y) on the base. Convert from IR's centre / +Y-up to
        # top-left / +Y-down.
        x, y = _overlay_position(transform, sequence)
        out_label = f"layer{cam_idx}"
        # ``enable`` makes the cam show only during its visible window
        # on the spine; outside that range the base shows through.
        end_time = align.cam_spine_start + align.cam_visible_seconds
        enable = f"between(t,{align.cam_spine_start:g},{end_time:g})"
        parts.append(f"[{base_label}][{scaled_label}]overlay=x={x:g}:y={y:g}:enable='{enable}'[{out_label}]")
        base_label = out_label

    if overlay_input_index is not None:
        parts.append(f"[{overlay_input_index}:v]setpts=PTS-STARTPTS[overlay_v]")
        parts.append(f"[{base_label}][overlay_v]overlay=0:0[withov]")
        base_label = "withov"

    if lower_third is not None:
        input_index, seconds = lower_third
        fade_start = max(0.0, seconds - LOWER_THIRD_FADE_SECONDS)
        parts.append(
            f"[{input_index}:v]format=rgba,"
            f"fade=t=out:st={fade_start:g}:d={LOWER_THIRD_FADE_SECONDS:g}:alpha=1[lt]"
        )
        parts.append(f"[{base_label}][lt]overlay=0:0:enable='lt(t,{seconds:g})'[withlt]")
        base_label = "withlt"

    parts.append(f"[{base_label}]null[final]")
    return ";".join(parts)


def _overlay_position(
    transform: Transform | None,
    sequence,  # type: ignore[no-untyped-def]
) -> tuple[float, float]:
    """Return the top-left (X, Y) ffmpeg ``overlay`` expects.

    No transform -> (0, 0): cam covers the base full-frame, matching
    today's stacked layout. With a transform: the cam centre lives at
    ``(W/2 + tx, H/2 - ty)`` (sequence-centre + IR offset, Y axis
    flipped); the top-left is that minus half the scaled clip's width
    / height.
    """
    if transform is None:
        return 0.0, 0.0
    scale = transform.scale
    seq_w = sequence.width
    seq_h = sequence.height
    centre_x = seq_w / 2.0 + transform.position[0]
    centre_y = seq_h / 2.0 - transform.position[1]  # flip
    clip_w = seq_w * scale
    clip_h = seq_h * scale
    return centre_x - clip_w / 2.0, centre_y - clip_h / 2.0


def _build_still_command(
    png_path: Path,
    *,
    seconds: float,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
) -> tuple[str, ...]:
    """Hold one canvas-sized PNG for ``seconds`` as a segment with silent
    audio, encoded exactly like a stage so the stitch stream-copies it.

    ``-t`` is an output option: both inputs (the looped image and
    ``anullsrc``) are infinite, and the hold is what bounds them.
    """
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loop",
        "1",
        "-framerate",
        _rate_string(sequence),
        "-i",
        str(png_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",
        f"{seconds:g}",
        "-filter_complex",
        "[0:v]format=yuv420p,setsar=1[final]",
        "-map",
        "[final]",
        "-map",
        "1:a",
        *_encode_args(sequence, youtube_preset=youtube_preset),
        str(output_path),
    )


def _build_segment_command(
    segment: Segment,
    *,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
) -> tuple[str, ...]:
    """Re-encode an intro / outro clip to the sequence's size and rate.

    Scale-to-fit and pad rather than stretch, ``setsar=1`` and ``fps=``
    so the segment agrees with the stages on everything the concat
    demuxer stream-copies -- which is what lets the MP4 path accept a
    clip at a different frame rate where the FCPXML path refuses one.
    Audio is the clip's own when it has any (``0:a?``).
    """
    graph = (
        f"[0:v]scale={sequence.width}:{sequence.height}:force_original_aspect_ratio=decrease,"
        f"pad={sequence.width}:{sequence.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fps={_rate_string(sequence)},format=yuv420p[final]"
    )
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-i",
        str(segment.asset.path),
        "-filter_complex",
        graph,
        "-map",
        "[final]",
        "-map",
        "0:a?",
        *_encode_args(sequence, youtube_preset=youtube_preset),
        str(output_path),
    )


def _build_concat_command(
    *,
    list_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    reencode_audio: bool = False,
) -> tuple[str, ...]:
    """Build the ``concat``-demuxer invocation that stitches the segment
    temps. The video is always a stream copy. ``reencode_audio`` (set
    when generated segments are present, issue #973) encodes the audio
    once over the whole match instead, so a card's ``anullsrc`` and a
    trim's camera audio need not share a sample rate; off, the argv is
    the plain ``-c copy`` it has always been."""
    codec_args = ("-c:v", "copy", "-c:a", "aac", "-b:a", "192k") if reencode_audio else ("-c", "copy")
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        *codec_args,
        "-movflags",
        "+faststart",
        str(output_path),
    )


__all__ = [
    "LOWER_THIRD_FADE_SECONDS",
    "FFmpegError",
    "Mp4RenderResult",
    "TimelinePlan",
    "plan_timeline",
    "render_mp4",
]
