"""The grid's free square (2026-10-02): what a cell no shooter fills shows.

A 2x2 grid with three shooters (or a 3x3 with five to eight) has cells no
tile reaches. They used to be black filler. Now the export can put
information there, chosen once for the whole render:

- ``stage`` -- the stage card: its number and name, the round count and
  the targets, from ``project.json`` (the same source as the slate's).
- ``splits`` -- the stage's split figures per shooter: draw, average and
  best split, with the best of each column marked in the split "good"
  colour, the way the Splits table marks it. The figures come from
  :func:`splitsmith.coach.statistic_splits` over the overlay's own shot
  data, the rule the per-tile summary uses; a figure a shooter does not
  have is a dash, never a zero.
- ``match`` -- the match card: its name, date and the squad.
- ``blank`` -- today's black.

Pure declaration (:func:`free_cell_groups`) plus one rasterization
(:func:`build_free_cell_still`) through the injected
:class:`~splitsmith.overlay_raster.Rasterizer`, the card renderer's own
path. A host with no browser keeps the black cell and says so through the
render's degradations, like every other card.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from ..coach import statistic_splits
from ..overlay_html import single_html
from ..overlay_layout import Anchor, CellScale, ColorToken, Element, Flow, Group, Role
from ..overlay_raster import Rasterizer
from ..overlay_theme import OverlayTheme
from ..stage_summary_data import TileStageData

logger = logging.getLogger(__name__)

FreeCellKind = Literal["blank", "stage", "splits", "match"]


@dataclass(frozen=True)
class FreeCellContext:
    """What a free square can say about one stage of one match."""

    stage_number: int
    stage_name: str
    match_name: str = ""
    match_date: str | None = None
    #: In roster (tile) order.
    shooters: tuple[str, ...] = ()
    expected_rounds: int | None = None
    paper_targets: int | None = None
    steel_targets: int | None = None
    #: Each shooter's data on this stage, keyed by label.
    tiles: dict[str, TileStageData] | None = None


@dataclass(frozen=True)
class _SplitRow:
    name: str
    draw: float | None
    avg: float | None
    best: float | None


#: The widest name the splits square's name column holds: the row is four
#: even columns, and on a 640 px square "Mathias" (seven) already reaches
#: the draw figure's edge.
NAME_MAX = 8


def short_names(labels: Sequence[str]) -> dict[str, str]:
    """First names, the way a squad talks; first name plus last initial
    where two shooters share one. Clipped to :data:`NAME_MAX` with a
    trailing dot: the square's four even columns have no room for
    "Mathias Axell" beside its figures (seen clipped on a real render)."""
    words = {label: label.split() or [label] for label in labels}
    counts: dict[str, int] = {}
    for parts in words.values():
        counts[parts[0]] = counts.get(parts[0], 0) + 1
    out: dict[str, str] = {}
    for label, parts in words.items():
        name = parts[0]
        if counts[name] > 1 and len(parts) > 1:
            name = f"{name} {parts[-1][0]}."
        out[label] = name if len(name) <= NAME_MAX else f"{name[: NAME_MAX - 1]}."
    return out


def _split_rows(ctx: FreeCellContext) -> list[_SplitRow]:
    rows: list[_SplitRow] = []
    names = short_names(ctx.shooters)
    for label in ctx.shooters:
        tile = (ctx.tiles or {}).get(label)
        if tile is None or not tile.has_shots:
            rows.append(_SplitRow(names[label], None, None, None))
            continue
        rest = statistic_splits(tile.shots)
        rows.append(
            _SplitRow(
                name=names[label],
                draw=tile.shots[0].split,
                avg=sum(rest) / len(rest) if rest else None,
                best=min(rest) if rest else None,
            )
        )
    return rows


def _bests(rows: Sequence[_SplitRow]) -> dict[str, float | None]:
    """The lowest value per column, only when two or more shooters have it
    (a best of one says nothing) -- the Splits table's own rule."""
    out: dict[str, float | None] = {}
    for column in ("draw", "avg", "best"):
        values = [getattr(r, column) for r in rows if getattr(r, column) is not None]
        out[column] = min(values) if len(values) >= 2 else None
    return out


def _figure(value: float | None, best: float | None, *, caption: str | None) -> Element:
    if value is None:
        return Element(role=Role.DETAIL, text="-", caption=caption, color=ColorToken.INK)
    marked = best is not None and value == best
    return Element(
        role=Role.DETAIL,
        text=f"{value:.2f}",
        caption=caption,
        color=ColorToken.SPLIT_GOOD if marked else ColorToken.INK,
    )


def _line(role: Role, text: str) -> Group:
    return Group(
        anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(Element(role=role, text=text),), align="center"
    )


def free_cell_groups(kind: FreeCellKind, ctx: FreeCellContext) -> tuple[Group, ...]:
    """What the free square says, as anchored groups; empty for ``blank``."""
    if kind == "stage":
        groups = [
            _line(Role.LABEL, f"Stage {ctx.stage_number:02d}"),
            _line(Role.IDENTITY, ctx.stage_name),
        ]
        if ctx.expected_rounds:
            groups.append(_line(Role.DETAIL, f"{ctx.expected_rounds} rounds"))
        targets = [
            f"{n} {kind_}" for n, kind_ in ((ctx.paper_targets, "paper"), (ctx.steel_targets, "steel")) if n
        ]
        if targets:
            groups.append(_line(Role.DETAIL, " · ".join(targets)))
        return tuple(groups)
    if kind == "match":
        groups = [_line(Role.IDENTITY, ctx.match_name or "Match")]
        if ctx.match_date:
            groups.append(_line(Role.DETAIL, ctx.match_date))
        if ctx.shooters:
            groups.append(_line(Role.DETAIL, " · ".join(ctx.shooters)))
        return tuple(groups)
    if kind == "splits":
        rows = _split_rows(ctx)
        if not any(r.draw is not None for r in rows):
            return (_line(Role.LABEL, "Splits"), _line(Role.DETAIL, "No audited shots on this stage"))
        best = _bests(rows)
        groups = [_line(Role.LABEL, f"Stage {ctx.stage_number:02d} · Splits")]
        for index, row in enumerate(rows):
            captions = ("Draw", "Avg", "Best") if index == 0 else (None, None, None)
            groups.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.GRID,
                    elements=(
                        Element(role=Role.DETAIL, text=row.name, caption="Shooter" if index == 0 else None),
                        _figure(row.draw, best["draw"], caption=captions[0]),
                        _figure(row.avg, best["avg"], caption=captions[1]),
                        _figure(row.best, best["best"], caption=captions[2]),
                    ),
                    align="left",
                )
            )
        return tuple(groups)
    return ()


def build_free_cell_still(
    groups: Sequence[Group],
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
) -> Image.Image | None:
    """The free square as a ``width x height`` RGB image on the theme's
    surface; ``None`` when there is nothing to say or the text could not be
    rasterized (the caller keeps the black cell)."""
    if not groups:
        return None
    html = single_html(groups, width=width, height=height, scale=CellScale.for_cell(height), theme=theme)
    try:
        png = rasterizer.png(html, width=width, height=height)
        with Image.open(io.BytesIO(png)) as rendered:
            text = rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning("could not rasterize the free square (%s); it stays black", exc)
        return None
    canvas = Image.new("RGBA", (width, height), (*theme.surface, 255))
    canvas.alpha_composite(text)
    return canvas.convert("RGB")
