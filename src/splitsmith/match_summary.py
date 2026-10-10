"""The match summary card (spec 2026-10-07-match-summary-design).

One full-frame card after the last stage of a single-shooter MP4: a strip of
match-wide figures over a row per stage. It reads the same per-stage data the
stage summary hold does (:class:`~splitsmith.stage_summary_data.TileStageData`)
and the same rules: average split is :func:`splitsmith.coach.statistic_splits`
over every audited stage, the draw is the first shot's split.

Only what is real: an absent figure is "-", never a zero, and nothing is
summed that the sport does not sum. There is no total stage time (IPSC ranks
by hit factor) and no match percentage (only per-stage percentages are
stored, and their mean is not the match result).
"""

from __future__ import annotations

import html
import io
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from .coach import statistic_splits
from .match_project import StageScorecard
from .overlay_html import _face_source
from .overlay_layout import MIN_FONT_SIZE, Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_summary_cell import _band_gap_extra, _counts_gap, _sgrid_gap, count_elements
from .overlay_theme import RGB, OverlayTheme
from .safe_area import is_upright, safe_area
from .stage_summary_data import TileStageData

logger = logging.getLogger(__name__)

#: The hold when the request names none.
DEFAULT_MATCH_SUMMARY_SECONDS = 6.0
#: Rows in one column of the stage table; past this it takes two.
ROWS_PER_COLUMN = 12
#: Hit and fault counts in the order the stage summary draws them.
HIT_KEYS = ("A", "C", "D", "M", "NS", "P")
_HIT_FIELDS = ("alphas", "charlies", "deltas", "misses", "no_shoots", "procedurals")
DASH = "-"


@dataclass(frozen=True)
class MatchSummaryRow:
    """One stage's line in the table; ``None`` is a figure that was never read."""

    number: int
    name: str
    time_seconds: float | None
    time_is_manual: bool
    hit_factor: float | None
    stage_pct: float | None
    draw: float | None
    avg_split: float | None
    dq: bool = False


@dataclass(frozen=True)
class MatchSummary:
    """What the card says. Built by :func:`build_match_summary`."""

    title: str
    label: str
    rows: tuple[MatchSummaryRow, ...]
    avg_split: float | None
    best_draw: float | None
    rounds: int | None
    #: Per count, the sum over the stages that reported it; ``None`` where
    #: no stage did (printed "-", never 0).
    hits: dict[str, int | None] | None
    #: How many stages each figure stands on, for :func:`coverage_lines`.
    split_stages: int
    scored_stages: int
    dq: bool = False
    duration_seconds: float = DEFAULT_MATCH_SUMMARY_SECONDS

    @property
    def stage_count(self) -> int:
        return len(self.rows)


def build_match_summary(
    stages: Sequence[tuple[str, TileStageData]],
    *,
    title: str,
    label: str,
    duration_seconds: float = DEFAULT_MATCH_SUMMARY_SECONDS,
) -> MatchSummary:
    """The card's figures from each stage's ``(name, data)`` in match order."""
    rows: list[MatchSummaryRow] = []
    every_split: list[float] = []
    draws: list[float] = []
    rounds = 0
    split_stages = 0
    scored_stages = 0
    hits: dict[str, int | None] = dict.fromkeys(HIT_KEYS)
    any_dq = False
    for name, tile in stages:
        card = tile.scorecard
        dq = bool(card is not None and card.dq)
        any_dq = any_dq or dq
        splits = statistic_splits(tile.shots) if tile.has_shots else []
        draw = tile.shots[0].split if tile.has_shots else None
        if tile.has_shots:
            split_stages += 1
            rounds += tile.shot_count
            every_split.extend(splits)
            if draw is not None:
                draws.append(draw)
        scored = card is not None and not dq
        if scored:
            assert card is not None
            counts = [getattr(card, field) for field in _HIT_FIELDS]
            # A stage stands behind the hit sums only when it reported a count;
            # a field no stage reported stays ``None``, as the stage summary
            # leaves an absent count out rather than drawing a zero.
            if any(c is not None for c in counts):
                scored_stages += 1
                for key, count in zip(HIT_KEYS, counts, strict=True):
                    if count is not None:
                        hits[key] = (hits[key] or 0) + count
        rows.append(
            MatchSummaryRow(
                number=tile.stage_number,
                name=name,
                time_seconds=tile.stage_time_seconds,
                time_is_manual=tile.stage_time_is_manual,
                hit_factor=card.hit_factor if scored and card is not None else None,
                stage_pct=card.stage_pct if scored and card is not None else None,
                draw=draw,
                avg_split=sum(splits) / len(splits) if splits else None,
                dq=dq,
            )
        )
    return MatchSummary(
        title=title,
        label=label,
        rows=tuple(rows),
        avg_split=sum(every_split) / len(every_split) if every_split else None,
        best_draw=min(draws) if draws else None,
        rounds=rounds if split_stages else None,
        hits=hits if scored_stages else None,
        split_stages=split_stages,
        scored_stages=scored_stages,
        dq=any_dq,
        duration_seconds=duration_seconds,
    )


def coverage_lines(summary: MatchSummary) -> list[str]:
    """The card's notes: how much of the match a figure stands on when it is
    not all of it, what the table's ``*`` means, and any DQ."""
    lines: list[str] = []
    total = summary.stage_count
    if 0 < summary.split_stages < total:
        lines.append(f"Splits from {summary.split_stages} of {total} stages")
    if 0 < summary.scored_stages < total:
        lines.append(f"Scores from {summary.scored_stages} of {total} stages")
    if any(row.time_is_manual and row.time_seconds is not None for row in summary.rows):
        lines.append("* stage time entered by hand")
    dq = [str(row.number) for row in summary.rows if row.dq]
    if dq:
        lines.append(f"DQ on stage{'s' if len(dq) > 1 else ''} {', '.join(dq)}")
    return lines


def _coverage(covered: int, total: int) -> Element | None:
    """The note beside a band when its figures stand on part of the match."""
    if 0 < covered < total:
        return Element(role=Role.LABEL, text=f"({covered} of {total} stages)")
    return None


def match_summary_groups(
    summary: MatchSummary,
    label: str,
    *,
    scale: CellScale,
    cell_width: int,
    cell_height: int,
    upright: bool = False,
) -> tuple[Group, ...]:
    """One shooter's tile on the grid's match summary (spec
    2026-10-08-grid-match-summary-design), in the stage summary hold's bands
    (:func:`splitsmith.overlay_summary_cell.summary_groups`) so the two read
    as one family: the name (a ``DQ`` plate when DQ'd on any stage), then
    **Scoring** (the counts summed over the stages that reported each) and
    **Splits** (Avg, Best draw, Rounds), each with "N of M stages" when it
    stands on part of the match. No ranking, no summed time, never a zero
    for a count nobody reported. A shooter with nothing recorded is a name.

    ``upright=True`` is a tile of an upright grid (issue #1394 part 2):
    Splits first, Avg / Best draw / Rounds as caption-value rows (the
    caption beside its figure, so "Best draw" never wraps), then Scoring
    with the counts wrapping to three columns.
    """
    identity: list[Element] = [Element(role=Role.IDENTITY, text=label)]
    if summary.dq:
        identity.append(Element(role=Role.VERDICT, text="DQ", emphasis=Emphasis.PLATE))
    groups: list[Group] = [
        Group(anchor=Anchor.TOP_CENTER, flow=Flow.ROW, elements=tuple(identity), align="left")
    ]
    total = summary.stage_count

    scoring = False
    scoring_groups: list[Group] = []
    if summary.hits is not None:
        hits = summary.hits
        totals = StageScorecard(
            alphas=hits["A"],
            charlies=hits["C"],
            deltas=hits["D"],
            misses=hits["M"],
            no_shoots=hits["NS"],
            procedurals=hits["P"],
        )
        counts = count_elements(totals)
        if counts and upright:
            scoring_groups = _upright_scoring_groups(counts, _coverage(summary.scored_stages, total), scale)
        elif counts:
            scoring = True
            head = [Element(role=Role.LABEL, text="Scoring", drop_priority=len(counts))]
            note = _coverage(summary.scored_stages, total)
            if note is not None:
                head.append(note)
            groups.append(
                Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=tuple(head), align="left")
            )
            groups.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=tuple(counts),
                    align="left",
                    gap=_counts_gap(scale),
                )
            )

    # All three or none, "-" for one that was never read, so the columns of
    # every tile on the card line up.
    splits: list[Element] = []
    if any(v is not None for v in (summary.avg_split, summary.best_draw, summary.rounds)):
        splits = [
            Element(role=Role.HEADLINE, text=_num(summary.avg_split), caption="Avg"),
            Element(role=Role.HEADLINE, text=_num(summary.best_draw), caption="Best draw"),
            Element(
                role=Role.HEADLINE,
                text=DASH if summary.rounds is None else str(summary.rounds),
                caption="Rounds",
            ),
        ]
    if upright:
        return _upright_tile(
            groups,
            splits,
            _coverage(summary.split_stages, total),
            scoring_groups,
            scale=scale,
            cell_height=cell_height,
        )
    if splits:
        head = [Element(role=Role.LABEL, text="Splits")]
        note = _coverage(summary.split_stages, total)
        if note is not None:
            head.append(note)
        groups.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.ROW,
                elements=tuple(head),
                align="left",
                margin_top=_band_gap_extra(cell_height) if scoring else None,
            )
        )
        groups.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.GRID,
                elements=tuple(splits),
                align="left",
                gap=_sgrid_gap(cell_width),
            )
        )
    return tuple(groups)


def _upright_scoring_groups(counts: list[Element], note: Element | None, scale: CellScale) -> list[Group]:
    """An upright grid tile's Scoring band: its label (and coverage note),
    then the counts three to a row. The counts drop first, then the note,
    then the label, as on the hold."""
    head = [Element(role=Role.LABEL, text="Scoring", drop_priority=len(counts) + 1)]
    if note is not None:
        head.append(replace(note, drop_priority=len(counts)))
    return [
        Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=tuple(head), align="left"),
        Group(
            anchor=Anchor.MIDDLE_CENTER,
            flow=Flow.GRID,
            elements=tuple(counts),
            align="left",
            gap=_counts_gap(scale),
            columns=3,
        ),
    ]


def _upright_tile(
    groups: list[Group],
    splits: list[Element],
    note: Element | None,
    scoring: list[Group],
    *,
    scale: CellScale,
    cell_height: int,
) -> tuple[Group, ...]:
    """An upright grid tile below its name: Splits as caption-value rows (the
    caption, then its figure, every figure lined up after the longest
    caption), then Scoring a band gap below. A tile too short for all of it
    gives up the Scoring band first, then the Rounds row, then the Splits
    band's coverage note; never Avg or Best draw."""
    priority = 1 + max(
        (e.drop_priority for g in scoring for e in g.elements if e.drop_priority is not None), default=-1
    )
    if splits:
        head = [Element(role=Role.LABEL, text="Splits")]
        rows: list[Element] = []
        for index, element in enumerate(splits):
            rounds = index == len(splits) - 1
            rows.append(
                Element(
                    role=Role.LABEL, text=element.caption or "", drop_priority=priority if rounds else None
                )
            )
            rows.append(replace(element, caption=None, drop_priority=priority if rounds else None))
        if note is not None:
            head.append(replace(note, drop_priority=priority + 1))
        groups.append(Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=tuple(head), align="left"))
        groups.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.GRID,
                elements=tuple(rows),
                align="left",
                gap=max(4, round(scale.caption * 0.8)),
                columns=2,
                content_columns=True,
            )
        )
    if scoring and splits:
        scoring[0] = replace(scoring[0], margin_top=_band_gap_extra(cell_height))
    groups.extend(scoring)
    return tuple(groups)


def _num(value: float | None, digits: int = 2) -> str:
    return DASH if value is None else f"{value:.{digits}f}"


def row_cells(row: MatchSummaryRow) -> list[str]:
    """A row's cells as printed: number, name, time, HF, %, draw, split."""
    time = _num(row.time_seconds)
    if row.time_seconds is not None and row.time_is_manual:
        time += "*"
    hf = "DQ" if row.dq else _num(row.hit_factor)
    pct = DASH if row.dq else _num(row.stage_pct, 1)
    return [f"{row.number:02d}", row.name, time, hf, pct, _num(row.draw), _num(row.avg_split)]


#: The squarest canvas (width:height) whose type still follows its height.
#: Sized by height, a typical twelve-stage card's headline row fits the page
#: from about 1.17:1 up and overflows a square one; 6:5 keeps every canvas
#: that fitted (6:5, 5:4, 4:3, 16:9 and wider) exactly as it was.
SIZE_ASPECT = (6, 5)

TABLE_HEADER = ["", "Stage", "Time", "HF", "%", "Draw", "Split"]
#: The table's fixed columns in ems (number, time, HF, %, draw, split, the
#: name's right padding) and the least a name keeps before its ellipsis.
_NUMBER_EMS = 2.2 + 4.3 * 4 + 3.9 + 0.6
_NAME_MIN_EMS = 9.0


def headline_figures(summary: MatchSummary) -> list[tuple[str, str]]:
    """``(caption, value)`` for the strip; splits lead, scoring follows."""
    figures = [
        ("Avg split", _num(summary.avg_split)),
        ("Best draw", _num(summary.best_draw)),
        ("Rounds", DASH if summary.rounds is None else str(summary.rounds)),
    ]
    if summary.hits is not None:
        figures.extend(
            (key, DASH if summary.hits[key] is None else str(summary.hits[key])) for key in HIT_KEYS
        )
    return figures


def _css_rgb(color: RGB) -> str:
    return f"rgb({color[0]},{color[1]},{color[2]})"


def match_summary_html(summary: MatchSummary, *, width: int, height: int, theme: OverlayTheme) -> str:
    """The card as one document, transparent where the backdrop shows. An
    upright canvas (taller than wide) takes its own layout,
    :func:`upright_match_summary_html`. Otherwise type follows the canvas
    height on any canvas at least :data:`SIZE_ASPECT` wide (6:5, 5:4, 4:3,
    16:9 and wider); a squarer one sizes it as a 6:5 card of its width,
    since by its height the headline figures run off the page. The table
    splits into two columns past :data:`ROWS_PER_COLUMN` so a long match
    never shrinks its rows below a readable size."""
    if is_upright(width, height):
        return upright_match_summary_html(summary, width=width, height=height, theme=theme)
    mono_url, mono_format, mono_weight = _face_source(theme.mono_font)
    display_url, display_format, display_weight = _face_source(theme.display_font)
    num, den = SIZE_ASPECT
    size = height if width * den >= height * num else width * den // num
    pad_x = round(width * 0.06)
    pad_y = round(height * 0.06)
    title_px = round(size * 0.06)
    label_px = round(size * 0.032)
    figure_px = round(size * 0.058)
    caption_px = round(size * 0.022)
    note_px = round(size * 0.022)
    columns = 1 if summary.stage_count <= ROWS_PER_COLUMN else 2
    per_column = max(1, math.ceil(summary.stage_count / columns))
    # The table takes what is left under the header and the strip.
    note_lines = max(2, len(coverage_lines(summary)))
    table_top = pad_y + title_px * 1.3 + figure_px * 1.2 + caption_px * 1.6 + note_px * 1.2 * note_lines
    table_height = height - pad_y - table_top
    column_gap = round(width * 0.04)
    column_width = (width - 2 * pad_x - (columns - 1) * column_gap) / columns
    # Ems a row needs: the fixed number columns plus room for a name.
    row_ems = _NUMBER_EMS + _NAME_MIN_EMS
    row_px = max(
        10,
        min(
            round(size * 0.032),
            math.floor(table_height / (per_column + 1) / 1.35),
            math.floor(column_width / row_ems),
        ),
    )
    ink = _css_rgb(theme.ink)
    ink_2 = _css_rgb(theme.ink_2)
    stroke = _css_rgb(theme.stroke)
    rule = _css_rgb(theme.rule)
    # The hit counts in the stage summary's own colours (``_COUNT_FIELDS``).
    hit_colour = {
        "A": _css_rgb(theme.split_good),
        "C": ink,
        "D": _css_rgb(theme.split),
        "M": _css_rgb(theme.accent_text),
        "NS": _css_rgb(theme.accent_text),
        "P": _css_rgb(theme.accent_text),
    }

    figures = "".join(
        '<div class="fig"><div class="v"'
        + (f' style="color: {hit_colour[caption]}"' if caption in hit_colour else "")
        + f">{html.escape(value)}</div>"
        f'<div class="c">{html.escape(caption)}</div></div>'
        for caption, value in headline_figures(summary)
    )
    notes = "".join(f'<div class="note">{html.escape(line)}</div>' for line in coverage_lines(summary))

    def table(rows: Sequence[MatchSummaryRow]) -> str:
        head = "".join(f"<th>{html.escape(h)}</th>" for h in TABLE_HEADER)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row_cells(row)) + "</tr>"
            for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    chunks = [summary.rows[i : i + per_column] for i in range(0, summary.stage_count, per_column)]
    tables = "".join(table(chunk) for chunk in chunks)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{
  font-family: "Splitsmith Mono";
  src: url("{mono_url}") format("{mono_format}");
  font-weight: {mono_weight};
}}
@font-face {{
  font-family: "Splitsmith Display";
  src: url("{display_url}") format("{display_format}");
  font-weight: {display_weight};
}}
html, body {{ margin: 0; width: {width}px; height: {height}px; background: transparent; overflow: hidden; }}
body {{
  box-sizing: border-box;
  padding: {pad_y}px {pad_x}px;
  color: {ink};
  font-family: "Splitsmith Mono", monospace;
  text-shadow: 0 {max(1, size // 360)}px {max(2, size // 180)}px {stroke};
}}
.head {{ display: flex; align-items: baseline; justify-content: space-between; gap: {pad_x // 2}px; }}
.title {{
  font-family: "Splitsmith Display", sans-serif;
  font-size: {title_px}px;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}}
.label {{ font-size: {label_px}px; color: {ink_2}; white-space: nowrap; }}
.strip {{
  display: flex;
  flex-wrap: nowrap;
  gap: {round(width * 0.025)}px;
  margin-top: {round(size * 0.03)}px;
  align-items: flex-end;
}}
.fig .v {{ font-size: {figure_px}px; line-height: 1.1; }}
.fig .c {{
  font-size: {caption_px}px;
  color: {ink_2};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: {round(caption_px * 0.3)}px;
}}
.note {{ font-size: {note_px}px; color: {ink_2}; margin-top: {round(note_px * 0.4)}px; }}
.notes {{ min-height: {round(note_px * 2.4)}px; margin-top: {round(size * 0.012)}px; }}
.tables {{
  display: grid;
  grid-template-columns: repeat({columns}, minmax(0, 1fr));
  column-gap: {column_gap}px;
  margin-top: {round(size * 0.01)}px;
  align-items: start;
}}
table {{ border-collapse: collapse; width: 100%; table-layout: fixed; font-size: {row_px}px; }}
th {{
  color: {ink_2};
  font-weight: normal;
  text-align: right;
  padding: 0 0 {round(row_px * 0.3)}px;
  font-size: {round(row_px * 0.8)}px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border-bottom: 1px solid {rule};
}}
td {{ text-align: right; padding: {round(row_px * 0.17)}px 0; line-height: 1.0; white-space: nowrap; }}
th:nth-child(1), td:nth-child(1) {{ width: 2.2em; text-align: left; color: {ink_2}; }}
th:nth-child(2), td:nth-child(2) {{
  text-align: left;
  width: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 0.6em;
}}
th:nth-child(n+3), td:nth-child(n+3) {{ width: 4.3em; }}
th:nth-child(5), td:nth-child(5) {{ width: 3.9em; }}
</style></head><body>
<div class="head">
<div class="title">{html.escape(summary.title)}</div>
<div class="label">{html.escape(summary.label)}</div>
</div>
<div class="strip">{figures}</div>
<div class="notes">{notes}</div>
<div class="tables">{tables}</div>
</body></html>"""


#: The upright table's columns: splits lead, as in the strip above it.
UPRIGHT_TABLE_HEADER = ["", "Stage", "Draw", "Split", "Time", "HF", "%"]
#: Characters of a stage name the upright table keeps before its ellipsis.
UPRIGHT_NAME_CHARS = 12
#: The upright table's fixed columns in ems (number, draw, split, time, HF,
#: %) and the room a name keeps from the Draw column. A mono figure is
#: 0.6 em a character, so a column holds its widest figure ("1.23",
#: "14.21*", "100.0") with about 0.8 em to spare.
_UPRIGHT_FIXED_EMS = (1.9, 3.2, 3.2, 4.4, 3.4, 3.6)
_UPRIGHT_NAME_PAD_EMS = 0.6
#: A mono face's advance per character, the bundled faces' own (0.6 em).
_MONO_ADVANCE_EMS = 0.6


def upright_row_cells(row: MatchSummaryRow) -> list[str]:
    """A row's cells in the upright table's order: number, name, draw,
    split, time, HF, %."""
    number, name, time, hf, pct, draw, split = row_cells(row)
    return [number, name, draw, split, time, hf, pct]


def _upright_fit_script(*, floor: int, bottom_line: int) -> str:
    """The upright card's fit: each figure row shrinks (one factor per row)
    until every value ends inside its own column, and the table shrinks
    until no figure overflows its cell and its last row sits above the
    safe area's bottom line, then spreads the height left over between its
    rows. Nothing goes under the legibility floor. Defined here and called
    by the rasterizer once the faces have loaded, like ``fit.js``."""
    return (
        "<script>window.__splitsmithFit = function () {" f"var FLOOR = {floor}; var LINE = {bottom_line};" """
function textRect(el) {
  var r = document.createRange();
  r.selectNodeContents(el);
  return r.getBoundingClientRect();
}
function overflows(cells, inner) {
  for (var i = 0; i < cells.length; i++) {
    var box = cells[i].getBoundingClientRect();
    var text = textRect(inner(cells[i]));
    if (text.width > 0 && (text.right > box.right + 0.5 || text.left < box.left - 0.5)) { return true; }
  }
  return false;
}
document.querySelectorAll('.figs').forEach(function (row) {
  var cells = row.querySelectorAll('.f');
  var inner = function (cell) { return cell.querySelector('.v'); };
  var base = parseFloat(getComputedStyle(row.querySelector('.v')).fontSize);
  var k = 1;
  while (overflows(cells, inner) && base * k * 0.97 >= FLOOR) {
    k *= 0.97;
    row.style.setProperty('--k', String(k));
  }
});
var table = document.querySelector('table');
if (table) {
  var tds = table.querySelectorAll('tbody td:not(.nm)');
  var self = function (cell) { return cell; };
  var rows = table.querySelectorAll('tbody tr').length;
  var size = parseFloat(getComputedStyle(table).fontSize);
  // A row's glyphs reach below its 1.0 line box: the text counts, not the box.
  var bottom = function () {
    return Math.max(table.getBoundingClientRect().bottom, textRect(table.tBodies[0]).bottom);
  };
  var past = function () { return bottom() > LINE + 0.5 || overflows(tds, self); };
  while (past() && size * 0.97 >= FLOOR) {
    size *= 0.97;
    table.style.fontSize = size + 'px';
  }
  var spare = LINE - bottom();
  if (rows > 0 && spare > 0) {
    var pad = Math.min(0.45 * size, spare / (2 * rows));
    table.style.setProperty('--pad', pad + 'px');
  }
}
};</script>"""
    )


def upright_match_summary_html(summary: MatchSummary, *, width: int, height: int, theme: OverlayTheme) -> str:
    """The card on an upright canvas (issue #1394, owner decisions
    2026-10-10): a small "Match summary" label over the title (two lines at
    most) and the shooter; Avg split / Best draw / Rounds alone on a row at
    10.4 % of the width, the hit counts on their own row; then one stage
    table that uses the height down to the safe area's bottom line, its
    splits leading (``# Stage Draw Split Time HF %``) and each stage's name
    inline, cut at :data:`UPRIGHT_NAME_CHARS` characters. Everything stays
    out of the platform safe area (:mod:`splitsmith.safe_area`): the right
    padding is the button column's width, the table stops at the bottom
    band."""
    area = safe_area(width, height)
    assert area is not None, "upright_match_summary_html takes an upright canvas"
    mono_url, mono_format, mono_weight = _face_source(theme.mono_font)
    display_url, display_format, display_weight = _face_source(theme.display_font)
    u = width / 100
    pad_x = round(6 * u)
    pad_right = max(pad_x, area.right)
    top = round(height * 0.07)
    kicker_px = max(MIN_FONT_SIZE, round(2.6 * u))
    title_px = round(8.2 * u)
    label_px = round(4.2 * u)
    figure_px = round(10.4 * u)
    count_px = round(6.4 * u)
    caption_px = max(MIN_FONT_SIZE, round(2.6 * u))
    note_px = max(MIN_FONT_SIZE, round(2.6 * u))
    content_width = width - pad_x - pad_right
    name_ems = UPRIGHT_NAME_CHARS * _MONO_ADVANCE_EMS + _MONO_ADVANCE_EMS + _UPRIGHT_NAME_PAD_EMS
    row_px = max(
        MIN_FONT_SIZE, min(round(4.4 * u), math.floor(content_width / (sum(_UPRIGHT_FIXED_EMS) + name_ems)))
    )
    ink = _css_rgb(theme.ink)
    ink_2 = _css_rgb(theme.ink_2)
    stroke = _css_rgb(theme.stroke)
    rule = _css_rgb(theme.rule)
    hit_colour = {
        "A": _css_rgb(theme.split_good),
        "C": ink,
        "D": _css_rgb(theme.split),
        "M": _css_rgb(theme.accent_text),
        "NS": _css_rgb(theme.accent_text),
        "P": _css_rgb(theme.accent_text),
    }

    def figure(caption: str, value: str) -> str:
        colour = f' style="color: {hit_colour[caption]}"' if caption in hit_colour else ""
        return (
            f'<div class="f"><div class="v"{colour}>{html.escape(value)}</div>'
            f'<div class="c">{html.escape(caption)}</div></div>'
        )

    figures = headline_figures(summary)
    splits_row = "".join(figure(caption, value) for caption, value in figures[:3])
    counts_row = "".join(figure(caption, value) for caption, value in figures[3:])
    counts = f'<div class="figs counts">{counts_row}</div>' if counts_row else ""
    notes = "".join(f'<div class="note">{html.escape(line)}</div>' for line in coverage_lines(summary))
    head = "".join(f"<th>{html.escape(h)}</th>" for h in UPRIGHT_TABLE_HEADER)
    body = "".join(
        "<tr>"
        + "".join(
            (
                f'<td class="nm"><span>{html.escape(cell)}</span></td>'
                if i == 1
                else f"<td>{html.escape(cell)}</td>"
            )
            for i, cell in enumerate(upright_row_cells(row))
        )
        + "</tr>"
        for row in summary.rows
    )
    # Column widths on ``<col>``: an em there is the table's own size, where
    # on a header cell it would be the smaller header type's.
    number_em, *figure_ems = _UPRIGHT_FIXED_EMS
    cols = f'<col style="width: {number_em}em"><col>' + "".join(
        f'<col style="width: {ems}em">' for ems in figure_ems
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{
  font-family: "Splitsmith Mono";
  src: url("{mono_url}") format("{mono_format}");
  font-weight: {mono_weight};
}}
@font-face {{
  font-family: "Splitsmith Display";
  src: url("{display_url}") format("{display_format}");
  font-weight: {display_weight};
}}
:root {{ {area.css_vars()} }}
html, body {{ margin: 0; width: {width}px; height: {height}px; background: transparent; overflow: hidden; }}
body {{
  box-sizing: border-box;
  padding: {top}px {pad_right}px {area.bottom}px {pad_x}px;
  color: {ink};
  font-family: "Splitsmith Mono", monospace;
  text-shadow: 0 {max(1, width // 360)}px {max(2, width // 180)}px {stroke};
}}
.kicker {{
  font-size: {kicker_px}px;
  color: {ink_2};
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.title {{
  font-family: "Splitsmith Display", sans-serif;
  font-size: {title_px}px;
  line-height: 1.05;
  margin-top: {round(0.6 * u)}px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}}
.label {{
  font-size: {label_px}px;
  color: {ink_2};
  margin-top: {round(0.8 * u)}px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.figs {{ display: grid; margin-top: {round(4 * u)}px; }}
.splits {{ grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: {round(3 * u)}px; }}
.splits .v {{ font-size: calc(var(--k, 1) * {figure_px}px); }}
.counts {{
  grid-template-columns: repeat(6, minmax(0, 1fr));
  column-gap: {round(1.5 * u)}px;
  margin-top: {round(2.5 * u)}px;
}}
.counts .v {{ font-size: calc(var(--k, 1) * {count_px}px); }}
.f {{ min-width: 0; }}
.v {{ line-height: 1.05; white-space: nowrap; }}
.c {{
  font-size: {caption_px}px;
  color: {ink_2};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: {round(0.4 * u)}px;
  white-space: nowrap;
}}
.notes {{ margin-top: {round(1.5 * u)}px; }}
.note {{ font-size: {note_px}px; color: {ink_2}; margin-top: {round(0.6 * u)}px; }}
table {{
  border-collapse: collapse;
  width: 100%;
  table-layout: fixed;
  font-size: {row_px}px;
  margin-top: {round(2.5 * u)}px;
}}
th {{
  color: {ink_2};
  font-weight: normal;
  text-align: right;
  font-size: max({MIN_FONT_SIZE}px, 0.62em);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 0 0.3em;
  border-bottom: 1px solid {rule};
  white-space: nowrap;
}}
td {{ text-align: right; padding: var(--pad, 0px) 0; line-height: 1.0; white-space: nowrap; }}
th:nth-child(1), td:nth-child(1) {{ text-align: left; color: {ink_2}; }}
th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
td.nm {{ color: {ink_2}; }}
td.nm span {{
  display: block;
  max-width: {UPRIGHT_NAME_CHARS + 1}ch;
  overflow: hidden;
  text-overflow: ellipsis;
}}
</style>
{_upright_fit_script(floor=MIN_FONT_SIZE, bottom_line=area.bottom_line)}
</head><body>
<div class="kicker">Match summary</div>
<div class="title">{html.escape(summary.title)}</div>
<div class="label">{html.escape(summary.label)}</div>
<div class="figs splits">{splits_row}</div>
{counts}
<div class="notes">{notes}</div>
<table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</body></html>"""


def match_summary_strip_html(title: str, *, width: int, height: int, theme: OverlayTheme) -> str:
    """The grid card's title strip: "Match summary" and the match name on one
    line, ``height`` tall, so the card does not read as one more stage hold."""
    mono_url, mono_format, mono_weight = _face_source(theme.mono_font)
    display_url, display_format, display_weight = _face_source(theme.display_font)
    pad_x = round(width * 0.03)
    title_px = round(height * 0.5)
    label_px = round(height * 0.3)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{
  font-family: "Splitsmith Mono";
  src: url("{mono_url}") format("{mono_format}");
  font-weight: {mono_weight};
}}
@font-face {{
  font-family: "Splitsmith Display";
  src: url("{display_url}") format("{display_format}");
  font-weight: {display_weight};
}}
html, body {{ margin: 0; width: {width}px; height: {height}px; background: transparent; overflow: hidden; }}
body {{
  box-sizing: border-box;
  padding: 0 {pad_x}px;
  display: flex;
  align-items: center;
  gap: {pad_x}px;
  color: {_css_rgb(theme.ink)};
}}
.label {{
  font-family: "Splitsmith Mono", monospace;
  font-size: {label_px}px;
  color: {_css_rgb(theme.ink_2)};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  white-space: nowrap;
}}
.title {{
  font-family: "Splitsmith Display", sans-serif;
  font-size: {title_px}px;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}}
</style></head><body>
<div class="label">Match summary</div>
<div class="title">{html.escape(title)}</div>
</body></html>"""


def upright_match_summary_strip_html(title: str, *, width: int, height: int, theme: OverlayTheme) -> str:
    """The grid card's title strip on an upright frame (issue #1394 part 2):
    a small "Match summary" label over the whole match name, which may take
    two lines and is never cut short. The name starts at half the strip's
    height on one line and shrinks (``window.__splitsmithFit``, called by the
    rasterizer once the faces have loaded) until it fits the strip in at
    most two lines, never under the legibility floor; only a name too long
    for two lines at the floor loses its end."""
    mono_url, mono_format, mono_weight = _face_source(theme.mono_font)
    display_url, display_format, display_weight = _face_source(theme.display_font)
    pad_x = round(width * 0.04)
    pad_y = round(height * 0.08)
    label_px = max(MIN_FONT_SIZE, round(height * 0.13))
    title_px = round(height * 0.5)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{
  font-family: "Splitsmith Mono";
  src: url("{mono_url}") format("{mono_format}");
  font-weight: {mono_weight};
}}
@font-face {{
  font-family: "Splitsmith Display";
  src: url("{display_url}") format("{display_format}");
  font-weight: {display_weight};
}}
html, body {{ margin: 0; width: {width}px; height: {height}px; background: transparent; overflow: hidden; }}
body {{
  box-sizing: border-box;
  padding: {pad_y}px {pad_x}px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  color: {_css_rgb(theme.ink)};
  text-shadow: 0 1px 3px {_css_rgb(theme.stroke)};
}}
.label {{
  font-family: "Splitsmith Mono", monospace;
  font-size: {label_px}px;
  color: {_css_rgb(theme.ink_2)};
  text-transform: uppercase;
  letter-spacing: 0.08em;
  white-space: nowrap;
}}
.title {{
  font-family: "Splitsmith Display", sans-serif;
  font-size: {title_px}px;
  line-height: 1.05;
  overflow-wrap: anywhere;
  padding-bottom: 0.12em;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}
</style>
<script>window.__splitsmithFit = function () {{
  var FLOOR = {MIN_FONT_SIZE};
  var title = document.querySelector('.title');
  var label = document.querySelector('.label');
  var style = getComputedStyle(document.body);
  var room = document.body.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  var size = parseFloat(getComputedStyle(title).fontSize);
  // The title's own lines, clamped or not: a range measures the text.
  function textHeight() {{
    var range = document.createRange();
    range.selectNodeContents(title);
    return range.getBoundingClientRect().height;
  }}
  function fits() {{
    var height = textHeight();
    var lines = Math.round(height / parseFloat(getComputedStyle(title).lineHeight));
    return lines <= 2 && label.getBoundingClientRect().height + height <= room + 0.5;
  }}
  while (!fits() && size * 0.97 >= FLOOR) {{
    size *= 0.97;
    title.style.fontSize = size + 'px';
  }}
}};</script>
</head><body>
<div class="label">Match summary</div>
<div class="title">{html.escape(title)}</div>
</body></html>"""


def build_match_summary_still(
    summary: MatchSummary,
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    rasterizer: Rasterizer | None,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image | None:
    """The card as a full-frame RGB still over ``backdrop`` (the last stage's
    final frame, blurred and dimmed, as the stage summary uses). No browser:
    the frame alone. ``None`` only when there is neither."""
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    text: Image.Image | None = None
    if rasterizer is not None:
        try:
            png = rasterizer.png(
                match_summary_html(summary, width=width, height=height, theme=theme),
                width=width,
                height=height,
            )
            with Image.open(io.BytesIO(png)) as rendered:
                text = rendered.convert("RGBA")
        except Exception as exc:  # noqa: BLE001 -- the frame alone is still a card
            logger.warning("could not rasterize the match summary (%s); the still composes without text", exc)
    if canvas is None and text is None:
        return None
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme.surface)
    composed = canvas.convert("RGBA")
    if text is not None:
        composed.alpha_composite(text)
    return composed.convert("RGB")


__all__ = [
    "DEFAULT_MATCH_SUMMARY_SECONDS",
    "MatchSummary",
    "MatchSummaryRow",
    "build_match_summary",
    "build_match_summary_still",
    "coverage_lines",
    "headline_figures",
    "match_summary_html",
    "row_cells",
    "upright_match_summary_html",
    "upright_row_cells",
]
