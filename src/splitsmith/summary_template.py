"""The stage and match summaries drawn by a Look's template (spec
``2026-10-16-summary-templates-design``).

``default`` is the engine's card (``overlay_summary_cell`` /
``match_summary``) and never reaches this module: the resolvers in
``looks`` answer ``None`` for it. A named variant is a template, loaded the
way an animated card is (``overlay_card.CardMotion``), so a still template
is one PNG and an animated one a clip keyed by its digest. A template that
cannot load costs the template, never the summary: the caller draws the
engine's card and says why.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from .events import confirmed
from .hud_extras import HudExtras, declared_data, hud_extras_data
from .identity import ResolvedIdentity
from .look_template import (
    TemplateContext,
    engine_block,
    shared_url,
    shooter_json,
    template_digest,
    theme_tokens,
)
from .looks import DEFAULT_VARIANT, Look, SummarySlot, summary_template_for
from .overlay_card import CardMotion, card_scale, note_card_degradation
from .overlay_html import single_css
from .overlay_hud import hud_safe_area_data, hud_stage_data
from .overlay_raster import Rasterizer, TemplateFrames
from .overlay_theme import OverlayTheme, theme_for
from .safe_area import safe_area
from .stage_summary_data import TileStageData, stage_summary_figures

if TYPE_CHECKING:
    from .match_summary import MatchSummary

logger = logging.getLogger(__name__)

_WHAT = {"summary": "the stage summary", "match_summary": "the match summary"}


def stage_summary_payload(
    tile: TileStageData, *, extras: HudExtras | None, keys: Sequence[str] = ()
) -> dict[str, Any]:
    """``data.stage``, ``data.figures`` and the declared provider keys for one
    stage's summary. Times are seconds from the beep (the HUD's own shape
    with the beep at 0); every region is a confirmed one."""
    regions = confirmed(tile.events)
    payload: dict[str, Any] = {
        "stage": hud_stage_data(tile.shots, beep_in_clip=0.0, events=regions),
        "figures": stage_summary_figures(tile).model_dump(),
    }
    payload.update(hud_extras_data(keys, tile.shots, beep_in_clip=0.0, events=regions, extras=extras))
    return payload


def match_summary_payload(summary: MatchSummary) -> dict[str, Any]:
    """``data.match``: the match summary as the engine card reads it."""
    match = asdict(summary)
    match.pop("duration_seconds", None)
    return {"match": match}


def summary_context(
    *,
    slot: SummarySlot,
    variant: str,
    text: str,
    payload: dict[str, Any],
    duration_seconds: float,
    width: int,
    height: int,
    fps: float,
    theme: OverlayTheme,
    shooters: Sequence[ResolvedIdentity] = (),
) -> TemplateContext:
    """``window.splitsmith`` for a summary template: ``data.card`` as a card
    gets it, ``data.shooters``, the payload, and on an upright canvas the
    platform safe area both as ``data.safe_area`` and as CSS variables."""
    data: dict[str, Any] = {
        "card": {
            "slot": slot,
            "variant": variant,
            "text": text,
            "info": [],
            "duration_seconds": duration_seconds,
        },
        "shooters": [shooter_json(shooter) for shooter in shooters],
        **payload,
    }
    css = single_css(width=width, height=height, scale=card_scale(height), theme=theme)
    area = safe_area(width, height)
    if area is not None:
        data["safe_area"] = hud_safe_area_data(area)
        css += f"\n:root {{ {area.css_vars()} }}"
    return TemplateContext(
        theme=theme_tokens(theme),
        data=data,
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=css),
        assets={"shared": shared_url()},
    )


def _motion(
    slot: SummarySlot,
    template: Path,
    context: TemplateContext,
    *,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    max_seconds: float,
) -> CardMotion | None:
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- a bad template costs the template, never the summary
        if frames is not None:
            frames.close()
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        logger.warning(
            "could not load %s through %s (%s); drawing the engine card", _WHAT[slot], template, exc
        )
        note_card_degradation(
            f"{_WHAT[slot]} drew the built-in card: its template {template.name} failed ({reason})"
        )
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)


@dataclass(frozen=True)
class ResolvedSummary:
    """A summary template and what it is handed; ``None`` from the
    resolvers below means the engine's card."""

    slot: SummarySlot
    template: Path
    context: TemplateContext
    duration_seconds: float


def resolve_stage_summary(
    look: Look,
    *,
    tile: TileStageData,
    label: str,
    variant: str,
    extras: HudExtras | None,
    duration_seconds: float,
    width: int,
    height: int,
    fps: float,
    shooters: Sequence[ResolvedIdentity] = (),
) -> ResolvedSummary | None:
    """The stage summary's template and context, or ``None`` for the engine's
    card: the ``default`` variant, or a variant the Look lacks (noted)."""
    template = summary_template_for(look, "summary", variant)
    if template is None:
        if variant != DEFAULT_VARIANT:
            note_card_degradation(f"the stage summary style {variant!r} is not in Look {look.name!r}")
        return None
    context = summary_context(
        slot="summary",
        variant=variant,
        text=label,
        payload=stage_summary_payload(tile, extras=extras, keys=declared_data(template)),
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        fps=fps,
        theme=theme_for(look),
        shooters=shooters,
    )
    return ResolvedSummary("summary", template, context, duration_seconds)


def resolve_match_summary(
    look: Look,
    *,
    summary: MatchSummary,
    variant: str,
    width: int,
    height: int,
    fps: float,
    shooters: Sequence[ResolvedIdentity] = (),
) -> ResolvedSummary | None:
    """The match summary's template and context, or ``None`` for the engine's card."""
    template = summary_template_for(look, "match_summary", variant)
    if template is None:
        if variant != DEFAULT_VARIANT:
            note_card_degradation(f"the match summary style {variant!r} is not in Look {look.name!r}")
        return None
    context = summary_context(
        slot="match_summary",
        variant=variant,
        text=summary.title,
        payload=match_summary_payload(summary),
        duration_seconds=summary.duration_seconds,
        width=width,
        height=height,
        fps=fps,
        theme=theme_for(look),
        shooters=shooters,
    )
    return ResolvedSummary("match_summary", template, context, summary.duration_seconds)


def summary_motion(resolved: ResolvedSummary, *, rasterizer: Rasterizer) -> CardMotion | None:
    """The template loaded for a render (still or animated), or ``None``
    (noted) when it fails: the caller draws the engine's card."""
    size = resolved.context.size
    return _motion(
        resolved.slot,
        resolved.template,
        resolved.context,
        width=size["width"],
        height=size["height"],
        fps=resolved.context.fps,
        rasterizer=rasterizer,
        max_seconds=resolved.duration_seconds,
    )


def summary_poster(resolved: ResolvedSummary, *, rasterizer: Rasterizer) -> Image.Image | None:
    """The template at its ``poster()`` as RGBA, for a still view of it (the
    stage export's held card, the export preview), or ``None`` (noted)."""
    size = resolved.context.size
    try:
        png = rasterizer.render_template(
            resolved.template, context=resolved.context, width=size["width"], height=size["height"]
        )
        with Image.open(io.BytesIO(png)) as rendered:
            return rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- a bad template costs the template, never the summary
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        note_card_degradation(
            f"{_WHAT[resolved.slot]} drew the built-in card: its template {resolved.template.name} "
            f"failed ({reason})"
        )
        return None


def stage_summary_motion(look: Look, *, rasterizer: Rasterizer, **kwargs: Any) -> CardMotion | None:
    """:func:`resolve_stage_summary` then :func:`summary_motion`."""
    resolved = resolve_stage_summary(look, **kwargs)
    return summary_motion(resolved, rasterizer=rasterizer) if resolved is not None else None


def match_summary_motion(look: Look, *, rasterizer: Rasterizer, **kwargs: Any) -> CardMotion | None:
    """:func:`resolve_match_summary` then :func:`summary_motion`."""
    resolved = resolve_match_summary(look, **kwargs)
    return summary_motion(resolved, rasterizer=rasterizer) if resolved is not None else None


__all__ = [
    "ResolvedSummary",
    "match_summary_motion",
    "match_summary_payload",
    "resolve_match_summary",
    "resolve_stage_summary",
    "stage_summary_motion",
    "stage_summary_payload",
    "summary_context",
    "summary_motion",
    "summary_poster",
]
