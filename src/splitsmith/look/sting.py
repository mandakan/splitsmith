"""A sting: a Look template laid over the boundary segment (issue #1245).

A transition of kind ``sting:<name>`` is the ``<name>`` variant of the
Look's ``transition`` slot, rendered as an alpha clip (the frames path the
animated cards use, :mod:`splitsmith.look.motion`) and composited over the
boundary's own crossfade, a plain ``fade`` of the same length, before the
segment's final ``format=yuv420p``. The template sees the transition
(``data.transition``: kind, name, duration, the labels either side of the
cut) and the shooters the cards see (``data.shooters``, each with only
its own logo), and sizes its
animation to the duration: ``duration()`` returns it, ``seek(t)`` drives
it, and the renderer samples ``ceil(duration * fps)`` frames.

Pure: nothing here shells out or writes a file. Both MP4 renderers call
:func:`sting_motion` while deciding a boundary, write the clip with
``look_motion.write_motion_clip`` and append :func:`sting_overlay_filters`
to the boundary command. A sting the Look lacks is ``None`` here and a
fade with a degradation there; a sting whose frames fail is a cut, like
any failed boundary.

A sting whose template declares ``splitsmith-frames`` (#1535) draws the
whole boundary from its edges' frames instead: :func:`sting_plan` decides
it as a :class:`FramesSting` (loaded once without frames, for its sounds
and its cache key), :func:`render_frames_sting` extracts both edges and
draws it in the boundary encode, and :func:`frames_sting_filters` lays
its clip over opaque black in place of the crossfade.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..composition import sting_name
from ..looks import Look, sting_template_for
from ..overlay.card import CardMotion, card_scale
from ..overlay.html import single_css
from ..overlay.raster import Rasterizer, TemplateFrames
from ..overlay.theme import OverlayTheme, theme_for
from .frames import edge_frame_urls, extraction_command, wants_frames, with_frames, written_frames
from .motion import MotionClipError, motion_overlay_filters, write_motion_clip
from .template import (
    TemplateContext,
    engine_block,
    shared_url,
    shooter_json,
    template_digest,
    theme_tokens,
)

logger = logging.getLogger(__name__)


def sting_context(
    *,
    kind: str,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    theme: OverlayTheme,
    shooters: Sequence[Any] = (),
    brand: dict[str, str | None] | None = None,
) -> TemplateContext:
    """What a ``transition`` slot template receives as ``window.splitsmith``:
    the transition as data, the shooters as the cards see them, the
    palette, the canvas and the engine block (its stylesheet, so a sting
    can draw in the overlay's typography). ``brand`` is the video maker's
    (``look_brand.brand_mark_json``), sent only when the ``wipe`` logo spot
    is on and there is one, so every other sting's context is as it was."""
    data: dict[str, Any] = {
        "transition": {
            "kind": kind,
            "name": sting_name(kind),
            "duration_seconds": seconds,
            "from": from_label,
            "to": to_label,
        },
        "shooters": [shooter_json(shooter) for shooter in shooters],
    }
    if brand is not None:
        data["brand"] = brand
    return TemplateContext(
        theme=theme_tokens(theme),
        data=data,
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(
            css=single_css(width=width, height=height, scale=card_scale(height), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def sting_motion(
    look: Look,
    kind: str,
    *,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    shooters: Sequence[Any] = (),
    brand: dict[str, str | None] | None = None,
) -> CardMotion | None:
    """Load the sting ``kind`` names and read how it renders over
    ``seconds``: frames are rendered lazily (a cached boundary pulls
    none), ``digest`` is what the segment cache keys the clip by.
    ``None`` when the Look (and the shipped default) has no such sting,
    or the template fails to load; the caller falls back to a fade."""
    template = sting_template_for(look, sting_name(kind))
    if template is None:
        logger.warning("Look %s has no sting %r; the transition is a fade", look.name, sting_name(kind))
        return None
    context = sting_context(
        kind=kind,
        seconds=seconds,
        from_label=from_label,
        to_label=to_label,
        width=width,
        height=height,
        fps=fps,
        theme=theme_for(look),
        shooters=shooters,
        brand=brand,
    )
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        if frames is not None:
            frames.close()
        logger.warning(
            "could not load the sting %r through %s (%s); the transition is a fade", kind, template, exc
        )
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)


def sting_overlay_filters(
    input_index: int, *, rate: str, seconds: float, source_label: str, out_label: str = "stung"
) -> tuple[list[str], str]:
    """Lay the sting clip (input ``input_index``) over ``source_label``
    (the crossfaded video) for the boundary's whole ``seconds``, from its
    first frame: the clip's timeline is the boundary's, so there is never
    a delay or an offset, whatever handle the edges had."""
    return motion_overlay_filters(
        input_index, rate=rate, seconds=seconds, source_label=source_label, out_label=out_label
    )


@dataclass
class FramesSting:
    """A sting that draws the whole boundary from its edges' frames
    (#1535). Decided before the edges exist: loaded once without frames
    for ``sounds()`` (a cached boundary still carries its cues) and keyed
    by ``digest``; drawn by :func:`render_frames_sting` in the boundary
    encode's ``prepare``."""

    template: Path
    context: TemplateContext
    digest: str
    sounds: tuple[dict[str, Any], ...]
    seconds: float
    width: int
    height: int
    fps: float

    def close(self) -> None:
        """Nothing to release: the decision-time load is closed at once."""


def sting_plan(
    look: Look,
    kind: str,
    *,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    shooters: Sequence[Any] = (),
    brand: dict[str, str | None] | None = None,
) -> CardMotion | FramesSting | None:
    """What a boundary's sting is: an overlay sting as :func:`sting_motion`
    loads it, a :class:`FramesSting` when the template declares
    ``splitsmith-frames``, or ``None`` (the caller falls back to a fade)."""
    template = sting_template_for(look, sting_name(kind))
    if template is None or not wants_frames(template):
        return sting_motion(
            look, kind, seconds=seconds, from_label=from_label, to_label=to_label, width=width,
            height=height, fps=fps, rasterizer=rasterizer, shooters=shooters, brand=brand,
        )  # fmt: skip
    context = sting_context(
        kind=kind, seconds=seconds, from_label=from_label, to_label=to_label, width=width,
        height=height, fps=fps, theme=theme_for(look), shooters=shooters, brand=brand,
    )  # fmt: skip
    try:
        loaded = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=seconds
        )
        try:
            sounds = loaded.sounds
        finally:
            loaded.close()
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        logger.warning(
            "could not load the sting %r through %s (%s); the transition is a fade", kind, template, exc
        )
        return None
    return FramesSting(
        template=template, context=context, digest=digest, sounds=sounds, seconds=seconds,
        width=width, height=height, fps=fps,
    )  # fmt: skip


def render_frames_sting(
    sting: FramesSting,
    *,
    tail_edge: Path,
    head_edge: Path,
    tail_pad_seconds: float,
    head_pad_seconds: float,
    rate: str,
    key: str,
    frames_root: Path,
    clip_path: Path,
    rasterizer: Rasterizer,
    ffmpeg_binary: str,
    run: Callable[[tuple[str, ...]], None],
) -> None:
    """Extract both edges under ``frames_root/key/{from,to}``, draw the
    sting with them and write its alpha clip to ``clip_path``. ``run``
    executes an ffmpeg argv and raises on failure (the driver's own
    runner). Raises :class:`MotionClipError` when an edge yields no frames
    or the template fails; the driver makes that a cut."""
    count = max(1, math.ceil(sting.seconds * sting.fps - 1e-9))
    lists: dict[str, list[str]] = {}
    for end, edge, pad in (("tail", tail_edge, tail_pad_seconds), ("head", head_edge, head_pad_seconds)):
        side = "from" if end == "tail" else "to"
        out_dir = frames_root / key / side
        out_dir.mkdir(parents=True, exist_ok=True)
        run(extraction_command(edge, out_dir, rate=rate, ffmpeg_binary=ffmpeg_binary))
        try:
            lists[side] = edge_frame_urls(
                written_frames(out_dir), count=count, fps=sting.fps, pad_seconds=pad, end=end, key=key  # type: ignore[arg-type]
            )
        except ValueError as exc:
            raise MotionClipError(f"{clip_path.name}: {exc}") from exc
    context = with_frames(
        sting.context, fps=sting.fps, count=count, from_urls=lists["from"], to_urls=lists["to"]
    )
    try:
        frames = rasterizer.render_template_frames(
            sting.template, context=context, width=sting.width, height=sting.height, fps=sting.fps,
            max_seconds=sting.seconds, frames_dir=frames_root,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 -- the template's failure is the clip's
        raise MotionClipError(f"{clip_path.name}: {exc}") from exc
    write_motion_clip(frames, out=clip_path, fps=sting.fps, ffmpeg_binary=ffmpeg_binary)


def frames_sting_filters(
    input_index: int, *, rate: str, seconds: float, width: int, height: int, out_label: str = "stung"
) -> tuple[list[str], str]:
    """A frames sting's video: its clip over opaque black for the whole
    boundary, in place of the crossfade (what it leaves transparent is
    black, by decision)."""
    canvas = f"color=c=black:s={width}x{height}:r={rate}:d={seconds:g},format=rgba[canvas]"
    parts, label = motion_overlay_filters(
        input_index, rate=rate, seconds=seconds, source_label="canvas", out_label=out_label
    )
    return [canvas, *parts], label


__all__ = [
    "FramesSting",
    "frames_sting_filters",
    "render_frames_sting",
    "sting_context",
    "sting_motion",
    "sting_overlay_filters",
    "sting_plan",
]
