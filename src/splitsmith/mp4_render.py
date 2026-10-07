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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from .composition import (
    Composition,
    ConnectedClip,
    Segment,
    SequenceFormat,
    Stage,
    SummaryHold,
    TitleCard,
    Transform,
    Transition,
    TransitionKind,
    xfade_name,
)
from .look_motion import MotionClipError, motion_overlay_filters, write_motion_clip
from .looks import load_look
from .overlay_card import (
    LOWER_THIRD_FADE_SECONDS,
    Card,
    CardMotion,
    card_backdrop,
    card_motion,
    compose_card,
    first_frame_image,
    lower_third_clip_filters,
    lower_third_filters,
)
from .overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .overlay_summary_cell import build_summary_still
from .overlay_theme import ThemeName, load_theme, theme_for
from .runtime import runtime
from .segment_cache import SegmentCache

logger = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class RenderStep:
    """One step of a render, for a progress line. ``index`` counts from 1
    over every timeline item plus the stitch (``total``). ``status`` is
    ``encoding`` as an encode starts, ``reused`` when the segment came
    from the cache and no encode ran, ``stitching`` for the final copy."""

    index: int
    total: int
    label: str
    status: Literal["encoding", "reused", "stitching"]


RenderProgress = Callable[[RenderStep], None]


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
    chapters: Sequence[ChapterMark] | None = None,
    segment_cache: SegmentCache | None = None,
    progress: RenderProgress | None = None,
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

    ``chapters`` (the #204 follow-up) embeds chapter atoms in the file:
    one ``[CHAPTER]`` per mark, on the timeline the renderer itself lays
    down (:func:`plan_timeline`), each ending where the next begins and
    the last at the end of the stitched output. QuickTime, VLC and
    YouTube's own chapter detection read them; the description text the
    sidecar writes stays the portable copy. ``None`` leaves the stitch
    argv exactly as it was.

    ``segment_cache`` keeps every encoded segment by the content of its
    command (:mod:`splitsmith.segment_cache`): a segment whose command
    and inputs are unchanged since an earlier render is not encoded
    again, and the stitch reads it from the cache. ``None`` encodes
    every segment into ``work_dir`` as before. ``progress`` hears each
    step (:class:`RenderStep`).
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
            degradations = (f"generated cards and summaries skipped: {exc.detail}",)
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
                    chapters=chapters,
                    segment_cache=segment_cache,
                    progress=progress,
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
            chapters=chapters,
            segment_cache=segment_cache,
            progress=progress,
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
    chapters: Sequence[ChapterMark] | None,
    segment_cache: SegmentCache | None = None,
    progress: RenderProgress | None = None,
) -> Mp4RenderResult:
    sequence = composition.sequence
    look = load_look(overlay_theme) if timeline.needs_rasterizer else None
    theme = theme_for(look) if look is not None else None
    fps = sequence.frame_rate_num / sequence.frame_rate_den
    shooters = composition.shooters
    segments: list[tuple[Path, float]] = []
    generated = False
    # Every item, plus two edges and the boundary per transition, plus the stitch.
    total_steps = len(timeline.items) + 3 * len(timeline.boundaries) + 1
    used_keys: set[str] = set()
    #: The cache key each encoded (or reused) segment path came from, so a
    #: boundary can key on its edges' identities rather than on files the
    #: cache's own LRU touch keeps re-dating (issue #1244).
    keys_by_path: dict[Path, str] = {}
    step = 0

    def next_step() -> int:
        nonlocal step
        step += 1
        return step

    def report(index: int, label: str, status: Literal["encoding", "reused", "stitching"]) -> None:
        if progress is not None:
            progress(RenderStep(index=index, total=total_steps, label=label, status=status))

    def encode(
        cmd: tuple[str, ...],
        out: Path,
        *,
        index: int,
        label: str,
        virtual_inputs: dict[str, str] | None = None,
        prepare: Callable[[], object] | None = None,
    ) -> Path:
        """Run ``cmd``, or reuse its segment from the cache; the path the
        stitch reads the segment from. ``prepare`` makes an input the
        command needs (a motion clip) and runs only when the segment is
        not cached; ``virtual_inputs`` is how the key stands in for that
        input before it exists."""
        if segment_cache is None:
            if prepare is not None:
                prepare()
            report(index, label, "encoding")
            _run(cmd, runner=runner)
            return out
        key = segment_cache.key(cmd, output_path=out, work_dir=work_dir, virtual_inputs=virtual_inputs)
        used_keys.add(key)
        hit = segment_cache.lookup(key)
        if hit is not None:
            report(index, label, "reused")
            keys_by_path[hit] = key
            return hit
        if prepare is not None:
            prepare()
        report(index, label, "encoding")
        partial = segment_cache.partial_path(key)
        target = str(out)
        try:
            _run(tuple(str(partial) if token == target else token for token in cmd), runner=runner)
            if not partial.is_file():
                raise FFmpegError(f"ffmpeg reported success but wrote no {label} segment")
            committed = segment_cache.commit(partial, key)
            keys_by_path[committed] = key
            return committed
        finally:
            partial.unlink(missing_ok=True)

    def clip_writer(motion: CardMotion, clip_path: Path) -> Callable[[], object]:
        # Idempotent: a boundary's edge and the card itself share one clip,
        # and the frames can only be pulled from the browser once.
        def write() -> None:
            if not clip_path.exists():
                write_motion_clip(motion.frames, out=clip_path, fps=fps, ffmpeg_binary=ffmpeg_binary)

        return write

    items: list[SpineItem] = list(timeline.items)
    live: dict[int, _Boundary] = {b.after_index: b for b in timeline.boundaries}
    killed: list[str] = []
    prepared: dict[int, _Prepared] = {}

    def prepare(item: SpineItem) -> _Prepared:
        """Everything an item's encode (or a boundary's edge of it) reads
        from disk: the lower third, a card's PNG or its backdrop and motion
        clip, a summary's still. A card that cannot be drawn is
        ``skipped`` (logged by overlay_card; a card is its text)."""
        if isinstance(item, _StageItem):
            prep = _Prepared(primary_audio=_has_audio_stream(item.plan.stage.primary.path))
            if item.lower_third is not None and rasterizer is not None and look is not None:
                lt_motion = card_motion(
                    item.lower_third,
                    slot="lower_third",
                    width=sequence.width,
                    height=sequence.height,
                    fps=fps,
                    look=look,
                    rasterizer=rasterizer,
                    max_seconds=item.lower_third.duration_seconds,
                    shooters=shooters,
                )
                if lt_motion is not None and not lt_motion.animated:
                    image = first_frame_image(lt_motion)
                    if image is not None:
                        png = work_dir / f"lower_third_{item.index:03d}.png"
                        image.save(png)
                        prep.lower_third = _LowerThirdInput(path=png, card=item.lower_third)
                elif lt_motion is not None:
                    clip_path = work_dir / f"lower_third_{item.index:03d}_motion.mov"
                    prep.lower_third = _LowerThirdInput(path=clip_path, card=item.lower_third, clip=True)
                    prep.motion = lt_motion
            return prep
        if isinstance(item, _StillItem):
            prep = _Prepared()
            if rasterizer is None or look is None:
                prep.skipped = True  # already recorded as a degradation up front
                return prep
            motion = card_motion(
                item.card,
                slot=item.kind,
                width=sequence.width,
                height=sequence.height,
                fps=fps,
                look=look,
                rasterizer=rasterizer,
                max_seconds=item.card_seconds,
                shooters=shooters,
            )
            if motion is None:
                prep.skipped = True
                return prep
            try:
                backdrop = _grab_backdrop(
                    timeline,
                    name=item.name,
                    stage_index=item.backdrop_stage_index,
                    at=item.backdrop_at,
                    work_dir=work_dir,
                    ffmpeg_binary=ffmpeg_binary,
                    runner=runner,
                )
                canvas = card_backdrop(backdrop, width=sequence.width, height=sequence.height, look=look)
            except BaseException:
                motion.close()
                raise
            if not motion.animated:
                text = first_frame_image(motion)
                if text is None:
                    prep.skipped = True
                    return prep
                png = work_dir / f"{item.name}.png"
                compose_card(text, canvas).save(png)
                prep.png = png
                return prep
            prep.backdrop_png = work_dir / f"{item.name}_backdrop.png"
            canvas.save(prep.backdrop_png)
            prep.clip_path = work_dir / f"{item.name}_motion.mov"
            prep.motion = motion
            return prep
        if isinstance(item, _SummaryItem):
            # The frame is grabbed whether or not there is a browser: the
            # blurred freeze without text is the grid's own degradation,
            # and a frame is a picture the viewer recognises.
            prep = _Prepared()
            backdrop = _grab_backdrop(
                timeline,
                name=item.name,
                stage_index=item.stage_index,
                at="tail",
                work_dir=work_dir,
                ffmpeg_binary=ffmpeg_binary,
                runner=runner,
            )
            image = build_summary_still(
                item.hold.data,
                item.hold.label,
                width=sequence.width,
                height=sequence.height,
                theme=theme if theme is not None else load_theme(overlay_theme),
                rasterizer=rasterizer,
                backdrop=backdrop,
                accent=shooters[0].accent if shooters else None,
            )
            if image is None:
                logger.warning(
                    "stage %d: no frame and no text to hold the summary on; skipped", item.stage_index
                )
                prep.skipped = True
                return prep
            png = work_dir / f"{item.name}.png"
            image.save(png)
            prep.png = png
            return prep
        return _Prepared()

    def encode_item(item: SpineItem, prep: _Prepared) -> Path | None:
        """The item's own segment, its cuts applied; ``None`` when the item
        is skipped. Closes the item's motion frames."""
        if isinstance(item, _StageItem):
            plan = item.plan
            if item.head_cut_seconds or item.tail_cut_seconds:
                plan = _narrow_plan(plan, head_cut=item.head_cut_seconds, tail_cut=item.tail_cut_seconds)
            lower_third = _trimmed_lower_third(prep.lower_third, head_cut=item.head_cut_seconds)
            stage_out = work_dir / f"{item.name}.mp4"
            label = item.plan.stage.name

            def command(lt: _LowerThirdInput | None) -> tuple[str, ...]:
                return _build_stage_command(
                    plan,
                    sequence=sequence,
                    output_path=stage_out,
                    ffmpeg_binary=ffmpeg_binary,
                    youtube_preset=youtube_preset,
                    lower_third=lt,
                    primary_audio=prep.primary_audio,
                )

            index = next_step()
            if prep.motion is None or lower_third is None:
                return encode(command(lower_third), stage_out, index=index, label=label)
            try:
                return encode(
                    command(lower_third),
                    stage_out,
                    index=index,
                    label=label,
                    virtual_inputs={str(lower_third.path): prep.motion.digest},
                    prepare=clip_writer(prep.motion, lower_third.path),
                )
            except MotionClipError as exc:
                # The stage is footage; a lower third that cannot be
                # drawn costs the title, never the stage.
                logger.warning("stage %d: %s; the lower third is dropped", item.index, exc)
                return encode(command(None), stage_out, index=index, label=label)
            finally:
                prep.motion.close()
        if isinstance(item, _StillItem):
            if prep.skipped:
                return None
            still_out = work_dir / f"{item.name}.mp4"
            if prep.motion is None:
                assert prep.png is not None
                cmd = _build_still_command(
                    prep.png,
                    seconds=item.duration_seconds,
                    sequence=sequence,
                    output_path=still_out,
                    ffmpeg_binary=ffmpeg_binary,
                    youtube_preset=youtube_preset,
                )
                return encode(cmd, still_out, index=next_step(), label=_step_label(item, timeline))
            assert prep.backdrop_png is not None and prep.clip_path is not None
            cmd = _build_motion_card_command(
                prep.backdrop_png,
                prep.clip_path,
                seconds=item.duration_seconds,
                sequence=sequence,
                output_path=still_out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
                clip_offset_seconds=item.head_cut_seconds,
            )
            try:
                return encode(
                    cmd,
                    still_out,
                    index=next_step(),
                    label=_step_label(item, timeline),
                    virtual_inputs={str(prep.clip_path): prep.motion.digest},
                    prepare=clip_writer(prep.motion, prep.clip_path),
                )
            except MotionClipError as exc:
                logger.warning("%s: %s; the card is skipped", item.name, exc)
                return None
            finally:
                prep.motion.close()
        if isinstance(item, _SummaryItem):
            if prep.skipped:
                return None
            assert prep.png is not None
            still_out = work_dir / f"{item.name}.mp4"
            cmd = _build_still_command(
                prep.png,
                seconds=item.duration_seconds,
                sequence=sequence,
                output_path=still_out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
            )
            return encode(cmd, still_out, index=next_step(), label=_step_label(item, timeline))
        clip_out = work_dir / f"{item.kind}.mp4"
        cmd = _build_segment_command(
            item.segment,
            sequence=sequence,
            output_path=clip_out,
            ffmpeg_binary=ffmpeg_binary,
            youtube_preset=youtube_preset,
        )
        return encode(cmd, clip_out, index=next_step(), label=item.kind)

    def encode_edge(
        index: int, item: SpineItem, prep: _Prepared, *, half: float, end: Literal["tail", "head"]
    ) -> Path:
        """A boundary's edge of ``item`` (issue #1244): ``2 * half`` seconds
        around the cut, rendered with the item's own builder so a stage's
        cams, overlay and lower third and a card's animation are what the
        crossfade shows. Raises when the item cannot provide one."""
        out = work_dir / f"edge_{index:03d}_{end}.mp4"
        label = f"transition edge ({end} of {item.name})"
        seconds = 2 * half
        if isinstance(item, _StageItem):
            plan = _edge_plan(item.plan, half=half, end=end)
            lower_third = prep.lower_third
            if lower_third is not None:
                if end == "head":
                    # The edge starts ``handle`` before the stage (the boundary
                    # holds a frame for the rest of the half), so the card
                    # opens then, not half a fade late.
                    lower_third = replace(
                        lower_third, delay_seconds=_edge_handle(item.plan, half=half, end="head")
                    )
                else:
                    skip = item.plan.effective_seconds - half
                    lower_third = (
                        replace(lower_third, skip_seconds=skip)
                        if skip < lower_third.card.duration_seconds
                        else None
                    )
            cmd = _build_stage_command(
                plan,
                sequence=sequence,
                output_path=out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
                lower_third=lower_third,
                primary_audio=prep.primary_audio,
            )
            if lower_third is not None and prep.motion is not None:
                return encode(
                    cmd,
                    out,
                    index=next_step(),
                    label=label,
                    virtual_inputs={str(lower_third.path): prep.motion.digest},
                    prepare=clip_writer(prep.motion, lower_third.path),
                )
            return encode(cmd, out, index=next_step(), label=label)
        if isinstance(item, _StillItem | _SummaryItem):
            if prep.skipped:
                raise _EdgeUnavailableError(f"{item.name} was skipped")
            if prep.motion is None:
                assert prep.png is not None
                cmd = _build_still_command(
                    prep.png,
                    seconds=seconds,
                    sequence=sequence,
                    output_path=out,
                    ffmpeg_binary=ffmpeg_binary,
                    youtube_preset=youtube_preset,
                )
                return encode(cmd, out, index=next_step(), label=label)
            assert (
                isinstance(item, _StillItem) and prep.backdrop_png is not None and prep.clip_path is not None
            )
            cmd = _build_motion_card_command(
                prep.backdrop_png,
                prep.clip_path,
                seconds=seconds,
                sequence=sequence,
                output_path=out,
                ffmpeg_binary=ffmpeg_binary,
                youtube_preset=youtube_preset,
                clip_delay_seconds=half if end == "head" else 0.0,
                clip_offset_seconds=item.card_seconds - half if end == "tail" else 0.0,
            )
            return encode(
                cmd,
                out,
                index=next_step(),
                label=label,
                virtual_inputs={str(prep.clip_path): prep.motion.digest},
                prepare=clip_writer(prep.motion, prep.clip_path),
            )
        raise _EdgeUnavailableError(f"{item.name} is a clip; it has no edge render")

    ordinal = 0
    for i, item in enumerate(items):
        prep = prepared.pop(i) if i in prepared else prepare(item)
        boundary = live.get(i)
        boundary_segment: Path | None = None
        if boundary is not None:
            ordinal += 1
            nxt = items[i + 1]
            if i + 1 not in prepared:
                prepared[i + 1] = prepare(nxt)
            half = boundary.duration_seconds / 2.0
            try:
                if prep.skipped or prepared[i + 1].skipped:
                    raise _EdgeUnavailableError("a neighbouring card was skipped")
                tail_edge = encode_edge(i, item, prep, half=half, end="tail")
                head_edge = encode_edge(i + 1, nxt, prepared[i + 1], half=half, end="head")
                boundary_out = work_dir / f"{boundary.name}.mp4"
                cmd = _build_boundary_command(
                    tail_edge,
                    head_edge,
                    kind=boundary.kind,
                    seconds=boundary.duration_seconds,
                    sequence=sequence,
                    output_path=boundary_out,
                    ffmpeg_binary=ffmpeg_binary,
                    youtube_preset=youtube_preset,
                    tail_pad_seconds=_missing_handle(item, half=half, end="tail"),
                    head_pad_seconds=_missing_handle(nxt, half=half, end="head"),
                )
                edge_keys = (
                    {str(tail_edge): keys_by_path[tail_edge], str(head_edge): keys_by_path[head_edge]}
                    if segment_cache is not None
                    else None
                )
                boundary_segment = encode(
                    cmd,
                    boundary_out,
                    index=next_step(),
                    label=f"transition {ordinal}",
                    virtual_inputs=edge_keys,
                )
            except (FFmpegError, MotionClipError, _EdgeUnavailableError) as exc:
                killed.append(f"transition after {item.name} failed to render ({exc}); rendered as a cut")
                logger.warning("%s", killed[-1])
                del live[i]
                items[i] = item = replace(item, tail_cut_seconds=0.0)
                items[i + 1] = replace(nxt, head_cut_seconds=0.0)
        segment = encode_item(item, prep)
        if segment is not None:
            segments.append((segment, item.duration_seconds))
            if not isinstance(item, _StageItem):
                generated = True
        if boundary_segment is not None and boundary is not None:
            segments.append((boundary_segment, boundary.duration_seconds))
            generated = True

    list_path = work_dir / "concat.txt"
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p, _ in segments),
        encoding="utf-8",
    )
    total_seconds = sum(seconds for _, seconds in segments)
    chapters_path: Path | None = None
    if chapters:
        chapters_path = work_dir / "chapters.ffmeta"
        chapters_path.write_text(chapter_metadata(chapters, total_seconds), encoding="utf-8")
    cmd = _build_concat_command(
        list_path=list_path,
        output_path=output_path,
        ffmpeg_binary=ffmpeg_binary,
        reencode_audio=generated,
        chapters_path=chapters_path,
    )
    report(total_steps, "the match video", "stitching")
    _run(cmd, runner=runner)
    if segment_cache is not None:
        segment_cache.evict(keep=used_keys)
    return Mp4RenderResult(
        output_path=output_path,
        duration_seconds=total_seconds,
        degradations=(*degradations, *timeline.degradations, *killed),
    )


def _step_label(item: _StillItem | _SummaryItem, timeline: TimelinePlan) -> str:
    """A card's progress label: the stage it belongs to by its own name
    (``Stage 3 slate``), never the zero-based segment index."""
    if isinstance(item, _SummaryItem):
        stage = timeline.stage(item.stage_index)
        return f"{stage.plan.stage.name} summary" if stage is not None else "summary"
    if item.name.startswith("slate_") and item.backdrop_stage_index is not None:
        stage = timeline.stage(item.backdrop_stage_index)
        if stage is not None:
            return f"{stage.plan.stage.name} slate"
    return {"title_page": "title page", "closing": "closing card"}.get(item.name, item.name.replace("_", " "))


def _has_audio_stream(path: Path) -> bool:
    """Whether ``path`` carries an audio stream, asked of ffprobe.

    Tolerant on purpose: only a probe that ran and reported *no* audio
    stream answers ``False``. A missing ffprobe, a file that is not
    there yet, or an unparseable answer all say ``True`` and let ffmpeg
    be the judge -- every primary this renderer has ever seen is a
    camera trim with audio, and the failure mode of guessing ``False``
    (a silent stage that also breaks the stitch's stream layout) is the
    worse one.
    """
    try:
        proc = subprocess.run(
            [
                runtime().ffprobe_binary,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(proc.stdout.strip())


#: How far before a *tail* backdrop's target frame the grab starts
#: reading. A seek straight to the last timestamp can land past the final
#: frame and write nothing (see ``compare/overlay_summary`` on the same
#: trap), so the read starts a window early and ``-update 1`` keeps the
#: last frame decoded. A head grab takes the first frame instead.
_BACKDROP_WINDOW_SECONDS = 0.5


def _grab_backdrop(
    timeline: TimelinePlan,
    *,
    name: str,
    stage_index: int | None,
    at: Literal["head", "tail"],
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
) -> Path | None:
    """Pull the frame a full-frame still sits on, or ``None`` when there is
    no stage to take it from or the grab produced no file.

    A title page and a slate take the first visible frame of the stage
    they precede; the closing card and a stage's summary take the last
    visible frame. A failed grab is not an error: the still composes on
    the theme's flat surface instead.
    """
    if stage_index is None:
        return None
    stage = timeline.stage(stage_index)
    if stage is None:
        return None
    plan = stage.plan
    out = work_dir / f"{name}_backdrop.png"
    out.unlink(missing_ok=True)
    primary = str(plan.stage.primary.path)
    if at == "head":
        # There is always a frame at or after the head seek, so take
        # exactly the first one; a window here would keep a frame half a
        # second into the stage instead of its visible head.
        window: tuple[str, ...] = (
            "-ss",
            f"{plan.head_trim_seconds:g}",
            "-i",
            primary,
            "-an",
            "-frames:v",
            "1",
        )
    else:
        seek = max(0.0, plan.head_trim_seconds + plan.effective_seconds - _BACKDROP_WINDOW_SECONDS)
        window = (
            "-ss",
            f"{seek:g}",
            "-t",
            f"{_BACKDROP_WINDOW_SECONDS:g}",
            "-i",
            primary,
            "-an",
            "-update",
            "1",
        )
    cmd = (ffmpeg_binary, "-hide_banner", "-y", *window, str(out))
    try:
        _run(cmd, runner=runner)
    except FFmpegError as exc:
        logger.warning("could not grab a backdrop frame for %s (%s); the still composes flat", name, exc)
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
    #: Footage the trim holds after the effective window (the handle a
    #: transition's tail edge reads past the tail pad, issue #1244).
    tail_trim_seconds: float = 0.0


@dataclass(frozen=True)
class _CamAlignment:
    cam: ConnectedClip
    cam_seek_seconds: float  # ``-ss`` value for the cam input
    cam_spine_start: float  # spine time when the cam first appears
    cam_visible_seconds: float  # how long the cam shows on the spine


ItemKind = Literal["intro", "title_page", "slate", "stage", "summary", "closing", "outro"]
StillKind = Literal["title_page", "slate", "closing"]
"""The generated full-frame cards: a subset of :data:`ItemKind` and of
``looks.CardSlot``, so a still item's kind names its Look template."""


@dataclass(frozen=True)
class _StageItem:
    """One stage on the spine, with the lower-third that rides its head."""

    index: int
    plan: _StagePlan
    lower_third: TitleCard | None
    kind: ItemKind = "stage"
    #: Seconds a transition took off either end (issue #1244); the item is
    #: encoded that much shorter and the boundary segment shows them.
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0

    @property
    def name(self) -> str:
        return f"stage_{self.index:03d}"

    @property
    def duration_seconds(self) -> float:
        return self.plan.effective_seconds - self.head_cut_seconds - self.tail_cut_seconds


@dataclass(frozen=True)
class _StillItem:
    """A generated full-frame card held on the spine for its duration.

    ``backdrop_stage_index`` / ``backdrop_at`` say which stage's frame the
    card sits on: the head of the stage it precedes, or the tail of the
    last stage for the closing card.
    """

    kind: StillKind
    name: str
    card: Card
    card_seconds: float
    backdrop_stage_index: int | None
    backdrop_at: Literal["head", "tail"] = "head"
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.card_seconds - self.head_cut_seconds - self.tail_cut_seconds


@dataclass(frozen=True)
class _SummaryItem:
    """The stage summary held after ``stage_index``'s action (issue #972):
    a still of that stage's last visible frame, blurred and dimmed, with
    the shooter's summary composed over it."""

    stage_index: int
    hold: SummaryHold
    kind: ItemKind = "summary"
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0

    @property
    def name(self) -> str:
        return f"summary_{self.stage_index:03d}"

    @property
    def duration_seconds(self) -> float:
        return self.hold.duration_seconds - self.head_cut_seconds - self.tail_cut_seconds


@dataclass(frozen=True)
class _ClipItem:
    """An intro / outro clip, re-encoded to the sequence."""

    kind: ItemKind
    segment: Segment
    head_cut_seconds: float = 0.0
    tail_cut_seconds: float = 0.0

    @property
    def name(self) -> str:
        return self.kind

    @property
    def duration_seconds(self) -> float:
        return self.segment.asset.metadata.duration_seconds - self.head_cut_seconds - self.tail_cut_seconds


SpineItem = _StageItem | _StillItem | _SummaryItem | _ClipItem


@dataclass(frozen=True)
class _Boundary:
    """A transition between ``items[after_index]`` and the next item
    (issue #1244): a crossfade of ``duration_seconds`` centred on the cut,
    each neighbour having given up half of it (its ``tail_cut_seconds`` /
    ``head_cut_seconds``), so the spine keeps its length."""

    after_index: int
    kind: TransitionKind
    duration_seconds: float

    @property
    def name(self) -> str:
        return f"boundary_{self.after_index:03d}"


@dataclass(frozen=True)
class TimelinePlan:
    """The ordered spine and its total length (issue #973).

    Pure: derived from the IR alone, so ``match_exports`` can quote the
    timeline length before anything is encoded. ``duration_seconds``
    counts every item as planned; :attr:`Mp4RenderResult.duration_seconds`
    is the same sum over what was actually written.
    """

    items: tuple[SpineItem, ...]
    boundaries: tuple[_Boundary, ...] = ()
    #: Transitions that did not fit, worded for ``Mp4RenderResult.degradations``.
    degradations: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        return sum(item.duration_seconds for item in self.items) + sum(
            b.duration_seconds for b in self.boundaries
        )

    def boundary_after(self, index: int) -> _Boundary | None:
        for boundary in self.boundaries:
            if boundary.after_index == index:
                return boundary
        return None

    @property
    def needs_rasterizer(self) -> bool:
        return any(
            isinstance(item, _StillItem | _SummaryItem)
            or (isinstance(item, _StageItem) and item.lower_third is not None)
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
    slate (when its title is one), the stage itself and its summary hold
    (when it has one), then the closing card and the outro. A lower-third
    title is attached to its stage rather than placed on the spine, since
    it overlays the head and adds no time."""
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
                card_seconds=composition.title_page.duration_seconds,
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
                    card_seconds=title.duration_seconds,
                    backdrop_stage_index=index,
                )
            )
        elif title is not None:
            lower_third = title
        items.append(_StageItem(index=index, plan=plan, lower_third=lower_third))
        if stage.summary is not None:
            items.append(_SummaryItem(stage_index=index, hold=stage.summary))
    if composition.closing is not None:
        items.append(
            _StillItem(
                kind="closing",
                name="closing",
                card=composition.closing,
                card_seconds=composition.closing.duration_seconds,
                backdrop_stage_index=len(composition.stages) - 1,
                backdrop_at="tail",
            )
        )
    if composition.outro is not None:
        items.append(_ClipItem(kind="outro", segment=composition.outro))
    boundaries, degradations = _place_boundaries(items, composition.transitions)
    return TimelinePlan(items=tuple(items), boundaries=boundaries, degradations=degradations)


def _place_boundaries(
    items: list[SpineItem], transitions: tuple[Transition, ...]
) -> tuple[tuple[_Boundary, ...], tuple[str, ...]]:
    """Turn the stage-indexed transitions into spine boundaries (issue
    #1244). A transition after stage i sits between the last item of
    stage i's run (its summary when it has one, else the stage) and the
    first of stage i+1's (its slate when it has one, else the stage).
    One that does not fit (:func:`_boundary_fit`) is reported and left
    out: a cut, never a clamped fade. The two neighbours of a placed
    boundary are replaced in ``items`` with their cuts set."""
    boundaries: list[_Boundary] = []
    degradations: list[str] = []
    for transition in sorted(transitions, key=lambda t: t.from_stage_index):
        prev_index = _last_item_of_stage(items, transition.from_stage_index)
        next_index = _first_item_of_stage(items, transition.to_stage_index)
        if prev_index is None or next_index is None or next_index != prev_index + 1:
            continue
        half = transition.duration_seconds / 2.0
        problem = _boundary_fit(
            items[prev_index], items[next_index], half=half, seconds=transition.duration_seconds
        )
        if problem is not None:
            degradations.append(problem)
            continue
        items[prev_index] = replace(items[prev_index], tail_cut_seconds=half)
        items[next_index] = replace(items[next_index], head_cut_seconds=half)
        boundaries.append(
            _Boundary(
                after_index=prev_index, kind=transition.kind, duration_seconds=transition.duration_seconds
            )
        )
    return tuple(boundaries), tuple(degradations)


def _stage_index_of(item: SpineItem) -> int | None:
    if isinstance(item, _StageItem):
        return item.index
    if isinstance(item, _SummaryItem):
        return item.stage_index
    if isinstance(item, _StillItem) and item.kind == "slate":
        return item.backdrop_stage_index
    return None


def _last_item_of_stage(items: list[SpineItem], stage_index: int) -> int | None:
    found = [i for i, item in enumerate(items) if _stage_index_of(item) == stage_index]
    return found[-1] if found else None


def _first_item_of_stage(items: list[SpineItem], stage_index: int) -> int | None:
    found = [i for i, item in enumerate(items) if _stage_index_of(item) == stage_index]
    return found[0] if found else None


def _boundary_fit(prev: SpineItem, nxt: SpineItem, *, half: float, seconds: float) -> str | None:
    """Why a transition of ``seconds`` cannot sit between ``prev`` and
    ``nxt``, or ``None`` when it can. A stage gives up ``half`` of its
    pad (the fade must not cover the last shot or the beep) and reads
    reads what handle the trim holds past it (:func:`_edge_handle`; the
    boundary pads a short handle with a held frame, so it is never a
    reason to refuse); a card gives up ``half`` of itself and its handle is
    its own frame, so it needs ``half`` to be at most half its length.
    Mirrors the FCPXML emitter's wording; reports, never clamps."""
    if isinstance(prev, _StageItem):
        name = prev.plan.stage.name
        pad = prev.plan.stage.tail_pad_seconds
        if half > pad:
            return (
                f"transition after stage {name!r} ({seconds:g}s) exceeds the stage's tail pad ({pad:g}s); "
                "increase the pad or shorten the transition: rendered as a cut"
            )
    elif half > prev.duration_seconds / 2.0:
        return (
            f"transition after {prev.name} ({seconds:g}s) exceeds half the card "
            f"({prev.duration_seconds / 2.0:g}s): rendered as a cut"
        )
    if isinstance(nxt, _StageItem):
        name = nxt.plan.stage.name
        pad = nxt.plan.stage.head_pad_seconds
        if half > pad:
            return (
                f"transition before stage {name!r} ({seconds:g}s) exceeds the stage's head pad ({pad:g}s); "
                "increase the pad or shorten the transition: rendered as a cut"
            )
    elif half > nxt.duration_seconds / 2.0:
        return (
            f"transition into {nxt.name} ({seconds:g}s) exceeds half the card "
            f"({nxt.duration_seconds / 2.0:g}s): rendered as a cut"
        )
    return None


def _narrow_plan(plan: _StagePlan, *, head_cut: float, tail_cut: float) -> _StagePlan:
    """The plan for a window of the same stage: ``head_cut`` later in
    (negative reads handle footage before the pad) and ``tail_cut``
    shorter (negative reads past the tail pad). The cams are recomputed
    from the new head, the same head-slip math :func:`_plan_stage` does."""
    head_trim = plan.head_trim_seconds + head_cut
    effective = plan.effective_seconds - head_cut - tail_cut
    return _StagePlan(
        stage=plan.stage,
        head_trim_seconds=head_trim,
        effective_seconds=effective,
        cam_alignments=_align_cams(plan.stage, head_trim_seconds=head_trim, effective_seconds=effective),
        tail_trim_seconds=plan.tail_trim_seconds + tail_cut,
    )


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

    return _StagePlan(
        stage=stage,
        head_trim_seconds=head_trim_seconds,
        effective_seconds=effective_seconds,
        cam_alignments=_align_cams(
            stage, head_trim_seconds=head_trim_seconds, effective_seconds=effective_seconds
        ),
        tail_trim_seconds=tail_trim_seconds,
    )


def _align_cams(
    stage: Stage, *, head_trim_seconds: float, effective_seconds: float
) -> tuple[_CamAlignment, ...]:
    cam_alignments: list[_CamAlignment] = []
    visible_head = head_trim_seconds  # source time of the visible head in the primary
    for sec in stage.secondaries:
        if not sec.enabled:
            # An angle carried for editing only: an MP4 has no use for it.
            continue
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
    return tuple(cam_alignments)


# --- command construction -------------------------------------------------


#: (input index, card seconds, is a clip, delay seconds, skip seconds): what
#: the stage filter graph needs to know about its lower third.
_LowerThirdGraph = tuple[int, float, bool, float, float]


class _EdgeUnavailableError(Exception):
    """A boundary's edge cannot be rendered (a skipped card, a clip item);
    the transition becomes a cut."""


@dataclass
class _Prepared:
    """What an item's encode needs on disk, made once and used by the
    item's own segment and by any boundary edge of it (issue #1244).
    ``motion`` is closed by the item's encode, the last user."""

    primary_audio: bool = True
    lower_third: _LowerThirdInput | None = None
    png: Path | None = None
    backdrop_png: Path | None = None
    clip_path: Path | None = None
    motion: CardMotion | None = None
    skipped: bool = False


@dataclass(frozen=True)
class _LowerThirdInput:
    """A rasterized lower-third (a PNG, or with ``clip`` an alpha clip
    from ``look_motion``) and the card that says how long it shows."""

    path: Path
    card: TitleCard
    clip: bool = False
    #: Issue #1244: the window opens ``delay_seconds`` late (a boundary's
    #: head edge) or drops ``skip_seconds`` already shown (the trimmed
    #: stage after that boundary). See ``overlay_card.lower_third_filters``.
    delay_seconds: float = 0.0
    skip_seconds: float = 0.0

    @property
    def shown_seconds(self) -> float:
        """How long the looped PNG input must last: until the window closes."""
        return self.delay_seconds + self.card.duration_seconds - self.skip_seconds


def _build_stage_command(
    plan: _StagePlan,
    *,
    sequence,  # type: ignore[no-untyped-def]
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
    lower_third: _LowerThirdInput | None = None,
    primary_audio: bool = True,
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

    **The audio is not taken from the seeked primary input.** Input-side
    ``-ss`` is exact for video (keyframe seek, then decode-and-discard to
    the target) but not for the audio of a stream-copied trim: measured
    on a real match, one stage's audio came out cut 0.42 s late while
    its video was cut exactly, so the segment's audio ran shorter than
    its video and the stitch's audio re-encode pulled every later stage
    0.4 s early. The primary is therefore opened a second time with no
    seek (``primary_audio``, the last input) and its audio is cut by
    ``atrim`` on decoded timestamps, which honours the trim's edit list;
    the video path is unchanged. ``primary_audio=False`` (a primary with
    no audio stream) maps no audio at all.
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

    lower_third_graph: _LowerThirdGraph | None = None
    if lower_third is not None:
        lower_third_index = 1 + len(plan.cam_alignments) + (1 if overlay_index is not None else 0)
        lower_third_graph = (
            lower_third_index,
            lower_third.card.duration_seconds,
            lower_third.clip,
            lower_third.delay_seconds,
            lower_third.skip_seconds,
        )
        if lower_third.clip:
            # A clip carries its own frames and length; the graph conforms
            # and holds it (``lower_third_clip_filters``).
            args += ["-i", str(lower_third.path)]
        else:
            args += [
                "-loop",
                "1",
                "-framerate",
                _rate_string(sequence),
                "-t",
                f"{lower_third.shown_seconds:g}",
                "-i",
                str(lower_third.path),
            ]

    audio_index: int | None = None
    if primary_audio:
        audio_index = (
            1 + len(plan.cam_alignments) + (1 if overlay_index is not None else 0) + (1 if lower_third else 0)
        )
        args += ["-i", str(stage.primary.path)]

    filter_graph = _build_stage_filter_graph(
        plan,
        sequence=sequence,
        overlay_input_index=overlay_index,
        lower_third=lower_third_graph,
        audio_input_index=audio_index,
    )

    args += ["-filter_complex", filter_graph, "-map", "[final]"]
    if audio_index is not None:
        args += ["-map", "[aout]"]
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
    lower_third: _LowerThirdGraph | None = None,
    audio_input_index: int | None = None,
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
        input_index, seconds, is_clip, lt_delay, lt_skip = lower_third
        if is_clip:
            lt_parts, base_label = lower_third_clip_filters(
                input_index,
                seconds,
                rate=_rate_string(sequence),
                source_label=base_label,
                delay_seconds=lt_delay,
                skip_seconds=lt_skip,
            )
        else:
            lt_parts, base_label = lower_third_filters(
                input_index, seconds, source_label=base_label, delay_seconds=lt_delay, skip_seconds=lt_skip
            )
        parts.extend(lt_parts)

    parts.append(f"[{base_label}]null[final]")

    # The primary's audio, cut from an unseeked read on decoded
    # timestamps (see ``_build_stage_command``): the same window the
    # video's ``-ss`` / ``-t`` describe, then re-based to zero so the
    # two streams start together.
    if audio_input_index is not None:
        parts.append(
            f"[{audio_input_index}:a]atrim=start={plan.head_trim_seconds:g}:duration={plan.effective_seconds:g},"
            "asetpts=PTS-STARTPTS[aout]"
        )
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


def _build_motion_card_command(
    backdrop_png: Path,
    clip: Path,
    *,
    seconds: float,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
    clip_offset_seconds: float = 0.0,
    clip_delay_seconds: float = 0.0,
) -> tuple[str, ...]:
    """An animated card: the template's alpha clip over its backdrop, the
    clip's last frame held to ``seconds``, silent audio, encoded like a
    stage. :func:`_build_still_command` with one more input.

    Issue #1244: ``clip_offset_seconds`` starts the clip that far in (a
    trimmed card after a boundary, or a tail edge where the clip has
    ended and its last frame holds; a filter that clones the last frame
    first, so an offset past the clip's end still shows it);
    ``clip_delay_seconds`` shows the backdrop alone that long before the
    clip begins (a head edge). Both zero emits the argv this always built."""
    rate = _rate_string(sequence)
    motion_parts, label = motion_overlay_filters(
        1,
        rate=rate,
        seconds=seconds,
        source_label="0:v",
        delay_seconds=clip_delay_seconds,
        offset_seconds=clip_offset_seconds,
    )
    graph = ";".join([*motion_parts, f"[{label}]format=yuv420p,setsar=1[final]"])
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loop",
        "1",
        "-framerate",
        rate,
        "-i",
        str(backdrop_png),
        "-i",
        str(clip),
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
        "2:a",
        *_encode_args(sequence, youtube_preset=youtube_preset),
        str(output_path),
    )


def _build_boundary_command(
    tail_edge: Path,
    head_edge: Path,
    *,
    kind: TransitionKind,
    seconds: float,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
    tail_pad_seconds: float = 0.0,
    head_pad_seconds: float = 0.0,
) -> tuple[str, ...]:
    """The boundary segment (issue #1244): ``tail_edge`` (the item before
    the cut) crossfaded into ``head_edge`` (the item after) over the whole
    ``seconds`` with ``xfade``, the two audio tracks crossfaded alike with
    ``acrossfade``; encoded like a stage so the stitch stays a stream
    copy. An edge whose trim had less handle than ``seconds / 2`` is
    shorter; ``tail_pad_seconds`` holds its last frame (and pads its audio
    with silence) and ``head_pad_seconds`` holds the head edge's first
    frame (delaying its audio) so both inputs span the fade."""
    parts: list[str] = []
    tail_v, tail_a, head_v, head_a = "0:v", "0:a", "1:v", "1:a"
    if tail_pad_seconds > 0.0:
        parts += [
            f"[0:v]tpad=stop_mode=clone:stop_duration={tail_pad_seconds:g}[tv]",
            f"[0:a]apad=pad_dur={tail_pad_seconds:g}[ta]",
        ]
        tail_v, tail_a = "tv", "ta"
    if head_pad_seconds > 0.0:
        parts += [
            f"[1:v]tpad=start_mode=clone:start_duration={head_pad_seconds:g}[hv]",
            f"[1:a]adelay={round(head_pad_seconds * 1000)}:all=1[ha]",
        ]
        head_v, head_a = "hv", "ha"
    parts += [
        f"[{tail_v}][{head_v}]xfade=transition={xfade_name(kind)}:duration={seconds:g}:offset=0,"
        "format=yuv420p[final]",
        f"[{tail_a}][{head_a}]acrossfade=d={seconds:g}:c1=tri:c2=tri[aout]",
    ]
    graph = ";".join(parts)
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-i",
        str(tail_edge),
        "-i",
        str(head_edge),
        "-filter_complex",
        graph,
        "-map",
        "[final]",
        "-map",
        "[aout]",
        "-t",
        f"{seconds:g}",
        *_encode_args(sequence, youtube_preset=youtube_preset),
        str(output_path),
    )


def _missing_handle(item: SpineItem, *, half: float, end: Literal["tail", "head"]) -> float:
    """How much of a boundary edge the item could not supply from footage:
    zero for a card (its handle is its own frame), ``half`` minus the
    trim's handle for a stage."""
    if isinstance(item, _StageItem):
        return half - _edge_handle(item.plan, half=half, end=end)
    return 0.0


def _trimmed_lower_third(lower_third: _LowerThirdInput | None, *, head_cut: float) -> _LowerThirdInput | None:
    """The lower third as the trimmed stage after a boundary shows it: what
    the head edge has not already shown. A card the edge showed in full is
    dropped: a looped PNG with ``-t 0`` runs forever and a negative ``-t``
    fails the encode (review of #1244)."""
    if lower_third is None or head_cut <= 0.0:
        return lower_third
    if head_cut >= lower_third.card.duration_seconds:
        return None
    return replace(lower_third, skip_seconds=head_cut)


def _edge_handle(plan: _StagePlan, *, half: float, end: Literal["tail", "head"]) -> float:
    """How much footage past the pad an edge can read: up to ``half``,
    bounded by what the trim holds (``tail_trim_seconds`` after the
    effective window, ``head_trim_seconds`` before it). At the default
    pads over the default trim buffers that is nothing, and the boundary
    pads the missing part with a held frame (review of #1244)."""
    available = plan.tail_trim_seconds if end == "tail" else plan.head_trim_seconds
    return max(0.0, min(half, available))


def _edge_plan(plan: _StagePlan, *, half: float, end: Literal["tail", "head"]) -> _StagePlan:
    """The stage window a boundary's edge render shows (issue #1244): the
    tail edge is the last ``half`` of the effective footage plus the handle
    past the tail pad; the head edge is the handle before the head pad
    plus the first ``half``. Each is ``half + handle`` long, ``2 * half``
    when the trim has the footage."""
    handle = _edge_handle(plan, half=half, end=end)
    if end == "tail":
        return _narrow_plan(plan, head_cut=plan.effective_seconds - half, tail_cut=-handle)
    return _narrow_plan(plan, head_cut=-handle, tail_cut=plan.effective_seconds - half)


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
    chapters_path: Path | None = None,
) -> tuple[str, ...]:
    """Build the ``concat``-demuxer invocation that stitches the segment
    temps. The video is always a stream copy. ``reencode_audio`` (set
    when generated segments are present, issue #973) encodes the audio
    once over the whole match instead, so a card's ``anullsrc`` and a
    trim's camera audio need not share a sample rate; off, the argv is
    the plain ``-c copy`` it has always been.

    ``chapters_path`` is an ffmetadata file (:func:`chapter_metadata`)
    read as a second input whose global metadata -- the chapters -- is
    mapped onto the output. It carries no streams, so ``-map 0`` pins
    every stream to the concat input rather than leaving the choice to
    ffmpeg's default selection; ``None`` adds nothing to the argv."""
    codec_args = ("-c:v", "copy", "-c:a", "aac", "-b:a", "192k") if reencode_audio else ("-c", "copy")
    chapter_args = ("-i", str(chapters_path), "-map_metadata", "1", "-map", "0") if chapters_path else ()
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
        *chapter_args,
        *codec_args,
        "-movflags",
        "+faststart",
        str(output_path),
    )


class ChapterMark(Protocol):
    """What :func:`render_mp4` needs of a chapter: where it starts on the
    rendered timeline and what to call it. ``youtube_sidecar.Chapter``
    satisfies it; the renderer does not import the sidecar."""

    @property
    def start_seconds(self) -> float: ...

    @property
    def title(self) -> str: ...


def _ffmetadata_escape(text: str) -> str:
    """Escape a value for ffmpeg's ffmetadata format, where ``=``, ``;``,
    ``#``, ``\\`` and a newline are structural."""
    out = []
    for ch in text:
        if ch in "=;#\\":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\\n")
        else:
            out.append(ch)
    return "".join(out)


def chapter_metadata(chapters: Sequence[ChapterMark], total_seconds: float) -> str:
    """The ffmetadata text for ``chapters`` on a timeline ``total_seconds``
    long: one ``[CHAPTER]`` block per mark in start order, millisecond
    timebase, each ending where the next begins and the last at the end.
    A mark at or past the end, or one that would not be after the
    previous one, is dropped rather than written as a zero-length or
    reversed chapter the muxer would then rewrite in its own way."""
    lines = [";FFMETADATA1"]
    end_ms = int(round(total_seconds * 1000))
    ordered = sorted(chapters, key=lambda c: c.start_seconds)
    starts = [int(round(c.start_seconds * 1000)) for c in ordered]
    previous: int | None = None
    for idx, chapter in enumerate(ordered):
        start = starts[idx]
        following = [s for s in starts[idx + 1 :] if s > start]
        end = following[0] if following else end_ms
        if start >= end_ms or end <= start or (previous is not None and start <= previous):
            continue
        previous = start
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_ffmetadata_escape(chapter.title)}",
        ]
    return "\n".join(lines) + "\n"


__all__ = [
    "LOWER_THIRD_FADE_SECONDS",
    "FFmpegError",
    "Mp4RenderResult",
    "TimelinePlan",
    "plan_timeline",
    "render_mp4",
]
