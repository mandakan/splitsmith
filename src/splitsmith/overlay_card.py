"""Generated cards for rendered output (issue #973).

A match title page, a closing card, a stage slate and a stage
lower-third are all one operation: declare what the card says as
``overlay_layout`` groups (:func:`card_groups`), hand that declaration
with the Look's palette and the engine stylesheet to the Look's template
for the card's slot (:func:`card_context`, the contract in
:mod:`splitsmith.look_template`), rasterize it in headless Chromium
through an injected :class:`~splitsmith.overlay_raster.Rasterizer`, and
composite the result over a backdrop. The shipped template draws the
engine's own markup, so the default Look renders what ``overlay_html``
renders; a user Look may draw the same data any way it likes. Nothing here
launches a browser, shells out to ffmpeg
or writes a file: :mod:`splitsmith.mp4_render` (and, later,
``compare/mp4_grid``) own the frame grab, the segment encode and the
splice. That is what lets one module serve both renderers.

**A card is its text.** The stage summary keeps its blurred freeze when
the rasterizer fails, because the freeze is still the shooter's own
frame. A slate with no text is a dimmed still of nothing in particular,
so :func:`build_card_still` returns ``None`` and the caller drops the
segment and records the degradation. A render never fails because of a
card.
"""

from __future__ import annotations

import functools
import io
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, ParamSpec, TypeVar

from PIL import Image

from .composition import MatchTitle, TitleCard
from .identity import ResolvedIdentity
from .look_brand import brand_json
from .look_template import (
    TemplateContext,
    engine_block,
    group_json,
    shared_url,
    shooter_json,
    template_digest,
    theme_tokens,
)
from .looks import CardSlot, Look, template_for
from .overlay_html import single_css
from .overlay_layout import Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer, TemplateFrames
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_theme import OverlayTheme, theme_for

logger = logging.getLogger(__name__)

Card = TitleCard | MatchTitle

#: How long a lower-third fades out for, at the end of its window. Shared
#: by both MP4 renderers so the two products fade alike.
LOWER_THIRD_FADE_SECONDS = 0.5


def _lower_third_window(
    seconds: float, delay_seconds: float, skip_seconds: float
) -> tuple[float, float, str]:
    """(fade start, window end, the ``enable`` expression) for a lower third
    shown for ``seconds`` from the segment's start; ``delay_seconds`` opens
    the window later and ``skip_seconds`` drops what an earlier segment
    already showed (issue #1244: a boundary's head edge starts the card
    d/2 in, the trimmed stage after it continues from d/2). With both at
    zero the strings are the ones every renderer emitted before."""
    end = delay_seconds + seconds - skip_seconds
    fade_start = max(delay_seconds, end - LOWER_THIRD_FADE_SECONDS)
    if delay_seconds == 0.0 and skip_seconds == 0.0:
        return fade_start, end, f"lt(t,{seconds:g})"
    return fade_start, end, f"between(t,{delay_seconds:g},{end:g})"


def lower_third_filters(
    input_index: int,
    seconds: float,
    *,
    source_label: str,
    delay_seconds: float = 0.0,
    skip_seconds: float = 0.0,
) -> tuple[list[str], str]:
    """The two ``-filter_complex`` chains that composite a lower-third over
    ``source_label`` and the label they end on: the PNG made ``rgba`` and
    faded out over its last :data:`LOWER_THIRD_FADE_SECONDS`, then an
    ``overlay`` disabled once ``seconds`` have passed (the window moved by
    ``delay_seconds`` / ``skip_seconds``, see :func:`_lower_third_window`).
    One spelling for both renderers, so their fades cannot drift apart."""
    fade_start, _end, enable = _lower_third_window(seconds, delay_seconds, skip_seconds)
    return [
        f"[{input_index}:v]format=rgba,fade=t=out:st={fade_start:g}:d={LOWER_THIRD_FADE_SECONDS:g}:alpha=1[lt]",
        f"[{source_label}][lt]overlay=0:0:enable='{enable}'[withlt]",
    ], "withlt"


def lower_third_clip_filters(
    input_index: int,
    seconds: float,
    *,
    rate: str,
    source_label: str,
    delay_seconds: float = 0.0,
    skip_seconds: float = 0.0,
) -> tuple[list[str], str]:
    """:func:`lower_third_filters` for an animated lower third: the clip
    conformed to ``rate`` and held like a motion card, then the same
    fade-out and the same ``enable`` window, so a still and an animated
    lower third leave the screen identically. A ``skip`` drops the clip's
    head after holding its last frame (so a skip past the animation still
    shows the card); a ``delay`` pads its start with transparent frames
    (the overlay must see a frame from t=0 or it would hold the picture)."""
    fade_start, end, enable = _lower_third_window(seconds, delay_seconds, skip_seconds)
    delay = (
        f"tpad=start_duration={delay_seconds:g}:start_mode=add:color=black@0.0,"
        if delay_seconds > 0.0
        else ""
    )
    if skip_seconds > 0.0:
        # Clone the last frame first, then skip: a skip past the clip's own
        # length still shows that frame (review of #1244).
        hold = (
            f"tpad=stop_mode=clone:stop_duration={seconds:g},trim=start={skip_seconds:g}:end={seconds:g},"
            "setpts=PTS-STARTPTS"
        )
    else:
        hold = f"tpad=stop_mode=clone:stop_duration={seconds:g},trim=0:{end:g}"
    return [
        f"[{input_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,{delay}{hold},"
        f"fade=t=out:st={fade_start:g}:d={LOWER_THIRD_FADE_SECONDS:g}:alpha=1[lt]",
        f"[{source_label}][lt]overlay=0:0:enable='{enable}'[withlt]",
    ], "withlt"


def _is_lower_third(card: Card) -> bool:
    return isinstance(card, TitleCard) and card.style == "lower-third"


def card_groups(card: Card) -> tuple[Group, ...]:
    """What the card says, as anchored groups.

    A full-frame card (a :class:`MatchTitle`, or a ``slate``
    :class:`TitleCard`) centres its text at
    :attr:`~splitsmith.overlay_layout.Role.IDENTITY` size -- the largest
    the scale has, the same weight the summary gives the shooter's name
    -- with one :attr:`~Role.DETAIL` line per ``info`` entry stacked under
    it. A ``lower-third`` sits bottom-left, left-aligned, its text on an
    accent plate (:attr:`~splitsmith.overlay_layout.Emphasis.PLATE`) at
    :attr:`~Role.HEADLINE` size so it reads over arbitrary footage without
    a halo, and the info lines plain beneath it.

    One group per line, not one ``ROW`` group with several elements: the
    lines stack vertically, and groups sharing an anchor are exactly the
    thing ``overlay_layout`` provides for that. Groups stack *away from
    the anchor's edge* in declaration order, so a bottom-anchored
    lower-third declares its info lines first (last line nearest the
    edge) and the name last, which is what puts the name on top when
    read. A middle anchor stacks top-down, so the title page declares
    the name first. Measured, not assumed: the first cut declared the
    lead first for both and the lower-third came out upside down.
    """
    if _is_lower_third(card):
        lead = Element(role=Role.HEADLINE, text=card.text, emphasis=Emphasis.PLATE)
        lines = [_line(Anchor.BOTTOM_LEFT, "left", text) for text in reversed(card.info)]
        lines.append(Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=(lead,), align="left"))
        return tuple(lines)
    lead = Element(role=Role.IDENTITY, text=card.text)
    groups = [Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(lead,), align="center")]
    groups.extend(_line(Anchor.MIDDLE_CENTER, "center", text) for text in card.info)
    return tuple(groups)


def _line(anchor: Anchor, align: Literal["left", "center"], text: str) -> Group:
    return Group(anchor=anchor, flow=Flow.ROW, elements=(Element(role=Role.DETAIL, text=text),), align=align)


def card_scale(height: int) -> CellScale:
    """The scale a card composes at: the whole frame is one cell."""
    return CellScale.for_cell(height)


def card_context(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    theme: OverlayTheme,
    shooters: Sequence[ResolvedIdentity] = (),
    brand: dict[str, str | None] | None = None,
) -> TemplateContext:
    """What the template for ``slot`` receives: the card as data
    (``data.card``), the engine's default declaration of it
    (``data.groups``, from :func:`card_groups`), the palette, the canvas,
    and the engine block a shipped template draws with."""
    scale = card_scale(height)
    data: dict[str, object] = {
        "card": {
            "slot": slot,
            "variant": card.variant,
            "text": card.text,
            "info": list(card.info),
            "duration_seconds": card.duration_seconds,
        },
        "groups": [group_json(g) for g in card_groups(card)],
        "shooters": [shooter_json(shooter) for shooter in shooters],
    }
    if brand is not None:
        # Your brand (``look_brand``): only on the cards that draw it, so
        # every other context, and its digest, is what it always was.
        data["brand"] = brand
    event_logo = getattr(card, "logo", None)
    if event_logo is not None and slot in ("title_page", "closing"):
        # The event's logo, a corner mark (``_shared/event.js``); its URL
        # is a ``logo`` value, which the sandbox mounts by that name.
        data["event"] = {"logo": Path(event_logo).resolve().as_uri()}
    return TemplateContext(
        theme=theme_tokens(theme),
        data=data,
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=single_css(width=width, height=height, scale=scale, theme=theme)),
        assets={"shared": shared_url()},
    )


_failures: ContextVar[list[str] | None] = ContextVar("splitsmith_card_failures", default=None)


@contextmanager
def card_failures() -> Iterator[list[str]]:
    """Collect, for the length of a render, every card a template's error
    left out, worded for the result's degradations. Without this a broken
    template dropped its card with only a log line and the export reported
    success (#1265). Nested renders each get their own list."""
    notes: list[str] = []
    token = _failures.set(notes)
    try:
        yield notes
    finally:
        _failures.reset(token)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def with_card_failures(
    add: Callable[[_R, list[str]], _R],
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Run a renderer inside :func:`card_failures` and hand its result and
    the failures to ``add`` (which puts them on the result's degradations)."""

    def wrap(render: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(render)
        def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with card_failures() as failed:
                result = render(*args, **kwargs)
            return add(result, failed) if failed else result

        return run

    return wrap


def _note_failure(what: str, template: Path, exc: BaseException) -> None:
    notes = _failures.get()
    if notes is None:
        return
    reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    notes.append(f"{what} was left out: its template {template.name} failed ({reason})")


def _rasterize(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    shooters: Sequence[ResolvedIdentity] = (),
) -> Image.Image | None:
    theme = theme_for(look)
    template = template_for(look, slot, card.variant)
    context = card_context(
        card,
        slot=slot,
        width=width,
        height=height,
        fps=fps,
        theme=theme,
        shooters=shooters,
        brand=brand_json(look, slot),
    )
    try:
        png_bytes = rasterizer.render_template(template, context=context, width=width, height=height)
        with Image.open(io.BytesIO(png_bytes)) as rendered:
            return rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning(
            "could not rasterize the card %r through %s (%s); it is skipped", card.text, template, exc
        )
        _note_failure(f"the {slot.replace('_', ' ')} card {card.text!r}", template, exc)
        return None


@dataclass(frozen=True)
class CardMotion:
    """A card's template, loaded: still or animated, and what the segment
    cache keys it by. ``frames`` is lazy; a cached segment never pulls
    one, so call :meth:`close` when done either way."""

    template: Path
    context: TemplateContext
    frames: TemplateFrames
    digest: str

    @property
    def animated(self) -> bool:
        return self.frames.duration > 0

    def close(self) -> None:
        self.frames.close()


def card_motion(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    max_seconds: float,
    shooters: Sequence[ResolvedIdentity] = (),
) -> CardMotion | None:
    """Load the card's template and read how it renders: one frame for a
    still, ``ceil(min(duration, max_seconds) * fps)`` for an animation.
    Frames are rendered lazily, so a cached segment costs no frame.
    ``None`` (logged) when the template cannot load; the card is skipped."""
    theme = theme_for(look)
    template = template_for(look, slot, card.variant)
    context = card_context(
        card,
        slot=slot,
        width=width,
        height=height,
        fps=fps,
        theme=theme,
        shooters=shooters,
        brand=brand_json(look, slot),
    )
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        if frames is not None:
            frames.close()
        logger.warning("could not load the card %r through %s (%s); it is skipped", card.text, template, exc)
        _note_failure(f"the {slot.replace('_', ' ')} card {card.text!r}", template, exc)
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)


def first_frame_image(motion: CardMotion) -> Image.Image | None:
    """The still path's text layer: the template's first frame as RGBA.
    ``None`` (logged) when rendering it raises. Closes the frames."""
    try:
        raw = next(iter(motion.frames.frames))
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning("could not rasterize the card through %s (%s); it is skipped", motion.template, exc)
        _note_failure("a card", motion.template, exc)
        return None
    finally:
        motion.close()
    return Image.frombytes("RGBA", (motion.frames.width, motion.frames.height), raw)


def card_backdrop(
    backdrop: Path | None,
    *,
    width: int,
    height: int,
    look: Look,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image:
    """The picture under a card: ``backdrop`` (a frame on disk, the first
    visible frame of the stage the card precedes) blurred and dimmed with
    the stage summary's own numbers (:mod:`splitsmith.overlay_still`);
    ``None``, or a frame that cannot be read, paints the Look's
    ``surface`` colour instead, so a failed frame grab costs the picture
    but never the card."""
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme_for(look).surface)
    return canvas


def compose_card(text: Image.Image, backdrop: Image.Image) -> Image.Image:
    """The text layer over the backdrop, as the RGB still a segment holds."""
    composed = backdrop.convert("RGBA")
    composed.alpha_composite(text)
    return composed.convert("RGB")


def build_card_still(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
    shooters: Sequence[ResolvedIdentity] = (),
) -> Image.Image | None:
    """Compose a full-frame card as a ``width x height`` RGB image, at the
    template's poster frame (the preview's and the thumbnails' view of
    a card; the renderers go through :func:`card_motion`).

    ``slot`` names the Look template that draws it (a :class:`MatchTitle`
    is ``title_page`` or ``closing``; a slate :class:`TitleCard` is
    ``slate``); see :func:`card_backdrop` for ``backdrop``.

    Returns ``None`` when the text could not be rasterized; see the
    module docstring for why that skips the card rather than degrading
    it.
    """
    text = _rasterize(
        card,
        slot=slot,
        width=width,
        height=height,
        fps=fps,
        look=look,
        rasterizer=rasterizer,
        shooters=shooters,
    )
    if text is None:
        return None
    canvas = card_backdrop(backdrop, width=width, height=height, look=look, blur_radius=blur_radius, dim=dim)
    return compose_card(text, canvas)


def build_lower_third(
    card: TitleCard,
    *,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    shooters: Sequence[ResolvedIdentity] = (),
) -> Image.Image | None:
    """Rasterize a lower-third as a transparent ``width x height`` RGBA
    image at its poster frame, for a preview to composite over a frame.
    No backdrop: the footage is the backdrop."""
    return _rasterize(
        card,
        slot="lower_third",
        width=width,
        height=height,
        fps=fps,
        look=look,
        rasterizer=rasterizer,
        shooters=shooters,
    )


__all__ = [
    "LOWER_THIRD_FADE_SECONDS",
    "Card",
    "CardMotion",
    "build_card_still",
    "build_lower_third",
    "card_backdrop",
    "card_context",
    "card_groups",
    "card_motion",
    "card_scale",
    "compose_card",
    "first_frame_image",
    "lower_third_clip_filters",
    "lower_third_filters",
]
