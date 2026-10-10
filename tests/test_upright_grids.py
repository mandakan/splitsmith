"""Upright compare-grid summaries (issue #1394 part 2): the stage hold and the
grid match summary on a canvas taller than wide.

The Chromium tests take the HTML the product's own ``build_hold_still`` /
``build_match_summary_grid_still`` hand their rasterizer and measure it laid
out in the real browser, fit script run, against the platform safe area
written out by hand: the bottom 13 % of the frame and the right 12 % from
40 % down.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare.mp4_grid import GridStagePlan, GridTile
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.compare.overlay_summary import build_hold_still, build_match_summary_grid_still
from splitsmith.events import ReloadFigure
from splitsmith.match_project import StageScorecard
from splitsmith.match_summary import build_match_summary
from splitsmith.overlay_layout import MIN_FONT_SIZE
from splitsmith.overlay_theme import load_theme
from splitsmith.stage_summary_data import TileShot, TileStageData

THEME = load_theme("splitsmith")
NAMES = ("Anders", "Bea", "Mathias", "Nils", "Olof", "Petra", "Rikard", "Sanna", "Tove")
NAMES += ("Ulla", "Viktor", "Wilma", "Xander", "Ylva", "Zlatan", "Kristoffersson")
SHAPES = {4: (2, 2), 9: (3, 3), 16: (4, 4)}
UPRIGHT = [(1080, 1920), (720, 1280)]


class _Recording:
    """Keeps every HTML document it is handed and answers a transparent PNG
    of the asked size, so the still composes as it would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((html, width, height))
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()


def _shots(*splits: float) -> tuple[TileShot, ...]:
    out, t = [], 0.0
    for i, split in enumerate(splits):
        t += split
        out.append(TileShot(time_from_beep=t, split=split, interval_class="draw" if i == 0 else "split"))
    return tuple(out)


def _card(**overrides) -> StageScorecard:
    values = {"hit_factor": 11.43, "stage_pct": 88.0, "alphas": 10, "charlies": 1, "deltas": 1}
    values.update({"misses": 0, "no_shoots": 0, "procedurals": 0})
    values.update(overrides)
    return StageScorecard(**values)


def _hold_tile(index: int, label: str, *, stage: int = 2) -> TileStageData:
    """A varied field: a tile with a scorecard and no shots (index 1), a
    confirmed reload (2), lit faults (7), a stage time entered by hand (3)."""
    if index == 1:
        return TileStageData(
            label=label, stage_number=stage, stage_time_seconds=4.0, scorecard=_card(hit_factor=12.0)
        )
    reloads = (ReloadFigure(event_id="r", duration=1.42, moving=False, exposed=1.42),) if index == 2 else ()
    card = (
        _card(alphas=4, charlies=4, deltas=2, misses=2, procedurals=1, hit_factor=0.93)
        if index == 7
        else _card()
    )
    return TileStageData(
        label=label,
        stage_number=stage,
        shots=_shots(0.50, 0.30, 0.37, 0.50, 0.41, 0.33),
        stage_time_seconds=4.5 + index / 10,
        stage_time_is_manual=index == 3,
        scorecard=card,
        reloads=reloads,
    )


def _hold_html(
    width: int, height: int, count: int, *, overrides: dict[int, TileStageData] | None = None
) -> tuple[str, SpriteGeometry]:
    rows, cols = SHAPES[count]
    geometry = SpriteGeometry(
        canvas_width=(width // cols) * cols, canvas_height=(height // rows) * rows, rows=rows, cols=cols
    )
    names = NAMES[:count]
    placements = [
        TilePlacement(label=n, row=i // cols, col=i % cols, present=True) for i, n in enumerate(names)
    ]
    data = {n: (overrides or {}).get(i) or _hold_tile(i, n) for i, n in enumerate(names)}
    fake = _Recording()
    build_hold_still(placements, data, {}, geometry, theme=THEME, rasterizer=fake)
    ((html, _, _),) = fake.calls
    return html, geometry


def _match_html(width: int, height: int, count: int, title: str = "Bromma Classifier"):
    rows, cols = SHAPES[count]
    names = NAMES[:count]
    tiles = tuple(
        GridTile(
            label=n,
            trim_path=None,
            beep_offset_in_clip=0.0,
            seek_seconds=0.0,
            lead_pad_seconds=0.0,
            source_duration_seconds=0.0,
            row=i // cols,
            col=i % cols,
        )
        for i, n in enumerate(names)
    )
    plan = GridStagePlan(
        stage_number=2,
        stage_name="Stage 2",
        tiles=tiles,
        duration_seconds=5.0,
        audio_label=names[0],
        rows=rows,
        cols=cols,
    )
    summaries = {
        n: build_match_summary(
            [(f"Stage {s}", _hold_tile(i, n, stage=s)) for s in (1, 2)],
            title=title,
            label=n,
            duration_seconds=5.0,
        )
        for i, n in enumerate(names)
    }
    fake = _Recording()
    build_match_summary_grid_still(
        plan, summaries, {}, width=width, height=height, title=title, theme=THEME, rasterizer=fake
    )
    (cells, cells_w, cells_h), (strip, _, strip_h) = fake.calls
    return cells, cells_w, cells_h, strip, strip_h


# --- the declared layout ------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (1080, 1080), (1280, 720)])
def test_square_and_wider_grids_take_no_upright_layout(width: int, height: int) -> None:
    html, _ = _hold_html(width, height, 9)
    cells, *_, strip, _ = _match_html(width, height, 9)
    for doc in (html, cells, strip):
        assert "--safe-bottom" not in doc
        assert "__splitsmithFitUniform = true" not in doc


@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_an_upright_hold_declares_splits_first_on_every_tile(width: int, height: int) -> None:
    html, _ = _hold_html(width, height, 9)
    for name in NAMES[:9]:
        if name == "Bea":  # no shots: a Scoring band alone
            continue
        cell = html[html.index(f">{name}<") :]
        assert cell.index(">Splits<") < cell.index(">Scoring<"), name
    assert "__splitsmithFitUniform = true" in html


# --- in the browser -----------------------------------------------------------------

_PROBE_JS = r"""() => {
  const out = [];
  document.querySelectorAll('.cell').forEach((cell, index) => {
    const tile = cell.getBoundingClientRect();
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent.trim();
      if (!text) continue;
      const el = node.parentElement;
      let hidden = false;
      for (let a = el; a; a = a.parentElement) {
        const s = getComputedStyle(a);
        if (s.display === 'none' || s.visibility === 'hidden') hidden = true;
      }
      if (hidden) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const r = range.getBoundingClientRect();
      if (r.width === 0) continue;
      // What is drawn: clipped by any ancestor that clips.
      let box = {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
      for (let a = el; a && a !== document.body; a = a.parentElement) {
        if (getComputedStyle(a).overflow !== 'visible') {
          const c = a.getBoundingClientRect();
          box = {left: Math.max(box.left, c.left), right: Math.min(box.right, c.right),
                 top: Math.max(box.top, c.top), bottom: Math.min(box.bottom, c.bottom)};
        }
      }
      if (box.right <= box.left) continue;
      const role = (String(el.className).match(/role-[a-z-]+|unit/) || ['caption'])[0];
      out.push({tile: index, text, role, size: parseFloat(getComputedStyle(el).fontSize), box,
                lines: range.getClientRects().length, width: r.width,
                cell: {left: tile.left, right: tile.right, top: tile.top, bottom: tile.bottom}});
    }
  });
  return out;
}"""


def _probe(html: str, *, width: int, height: int, tmp_path: Path, js: str = _PROBE_JS):
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    page_path = tmp_path / "grid.html"
    page_path.write_text(html, encoding="utf-8")
    try:
        with ChromiumRasterizer() as rasterizer:
            context = rasterizer._live_browser().new_context(viewport={"width": width, "height": height})
            try:
                page = context.new_page()
                page.goto(page_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                return page.evaluate(js)
            finally:
                context.close()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


def _misplaced(items: list[dict], *, frame_width: int, frame_height: int, top: int = 0) -> list[str]:
    """Text outside its own tile or in the platform safe area, written out
    here rather than read from the module under test."""
    bottom_line = frame_height - round(frame_height * 0.13)
    right_line = frame_width - round(frame_width * 0.12)
    right_top = round(frame_height * 0.40)
    out = []
    for item in items:
        b, c = item["box"], item["cell"]
        bottom = b["bottom"] + top
        if b["left"] < c["left"] - 0.5 or b["right"] > c["right"] + 0.5 or b["top"] < c["top"] - 0.5:
            out.append(f"outside its tile: {item['tile']}:{item['text']}")
        elif b["bottom"] > c["bottom"] + 0.5:
            out.append(f"outside its tile: {item['tile']}:{item['text']}")
        elif bottom > bottom_line + 0.5:
            out.append(f"in the bottom band: {item['tile']}:{item['text']}")
        elif b["right"] > right_line + 0.5 and bottom > right_top:
            out.append(f"in the button column: {item['tile']}:{item['text']}")
    return out


def _one_scale(items: list[dict]) -> dict[str, set[float]]:
    """Every size each role draws at across the grid."""
    sizes: dict[str, set[float]] = {}
    for item in items:
        sizes.setdefault(item["role"], set()).add(round(item["size"], 1))
    return sizes


_HOLD_CASES = [(w, h, n) for w, h in UPRIGHT for n in (4, 9)]
#: Sixteen shooters as well: the tightest tiles, where the fit has to drop.
_SAFE_CASES = [(w, h, n) for w, h in UPRIGHT for n in (4, 9, 16)]


def _hold_items(tmp_path: Path, width: int, height: int, count: int) -> list[dict]:
    html, geometry = _hold_html(width, height, count)
    return _probe(html, width=geometry.canvas_width, height=geometry.canvas_height, tmp_path=tmp_path)


def _match_items(tmp_path: Path, width: int, height: int, count: int) -> tuple[list[dict], int]:
    cells, cells_w, cells_h, _, strip_h = _match_html(width, height, count)
    return _probe(cells, width=cells_w, height=cells_h, tmp_path=tmp_path), strip_h


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _SAFE_CASES)
def test_an_upright_hold_keeps_every_figure_in_its_tile_and_out_of_the_safe_area(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    items = _hold_items(tmp_path, width, height, count)
    assert _misplaced(items, frame_width=width, frame_height=height) == []
    assert min(item["size"] for item in items) >= MIN_FONT_SIZE
    # Every tile with shots keeps its Best / Avg / Worst / Draw.
    for tile in range(count):
        if tile % 9 != 1:
            assert {"0.30", "0.38", "0.50"} <= {i["text"] for i in items if i["tile"] == tile}, tile
    # Nothing is cut short: the stage time entered by hand (``4.80s
    # (manual)``) draws whole, and no figure loses its end to an ellipsis.
    assert any(item["text"] == "4.80s (manual)" for item in items)
    cut = [item["text"] for item in items if item["box"]["right"] - item["box"]["left"] < item["width"] - 0.5]
    assert cut == []


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _HOLD_CASES)
def test_an_upright_hold_draws_every_tile_at_one_scale(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    """A tile with less to say (Bea: a scorecard, no shots) never draws its
    figures larger than its neighbours'."""
    items = _hold_items(tmp_path, width, height, count)
    sizes = _one_scale(items)
    for role in ("role-headline", "role-detail", "role-label", "caption", "unit"):
        assert len(sizes.get(role, {0})) == 1, (role, sizes.get(role))
    hit_factor = {item["tile"]: item["size"] for item in items if item["text"] in {"12.00", "11.43", "0.93"}}
    assert len(hit_factor) == count and len(set(hit_factor.values())) == 1, hit_factor


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _HOLD_CASES)
def test_an_upright_hold_leads_every_tile_with_its_splits(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    items = _hold_items(tmp_path, width, height, count)
    for tile in range(count):
        own = [item for item in items if item["tile"] == tile]
        if tile == 1:
            assert not any(i["text"] == "Splits" for i in own)
            continue
        splits = min(i["box"]["top"] for i in own if i["text"] == "Splits")
        scoring = [i["box"]["top"] for i in own if i["text"] == "Scoring"]
        assert scoring and splits < min(scoring), tile
        # Hit factor and time one size down from the split figures.
        assert _size(own, "0.93" if tile == 7 else "11.43") < _size(own, "0.30")


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_a_tile_far_past_a_real_stage_still_keeps_out_of_the_safe_area(
    tmp_path: Path, width: int, height: int
) -> None:
    """Counts and a reload far past a real stage in the bottom-right tile of
    sixteen, the tightest tile there is: still nothing in the safe area."""
    base = _hold_tile(15, "Kristoffersson")
    big = TileStageData(
        label="Kristoffersson",
        stage_number=2,
        shots=base.shots,
        stage_time_seconds=base.stage_time_seconds,
        scorecard=_card(
            alphas=123456, charlies=123456, deltas=123456, misses=12, no_shoots=12, procedurals=12
        ),
        reloads=(ReloadFigure(event_id="r", duration=11.42, moving=False, exposed=11.42),),
    )
    html, geometry = _hold_html(width, height, 16, overrides={15: big})
    items = _probe(html, width=geometry.canvas_width, height=geometry.canvas_height, tmp_path=tmp_path)
    assert _misplaced(items, frame_width=width, frame_height=height) == []


_FILL_JS = r"""() => [...document.querySelectorAll('.cell')].map((cell) => {
  // The band under the name over the track it has: the cell less its name,
  // its gaps and the safe area's padding.
  const stack = cell.querySelector('.anchor-middle-center');
  const track = parseFloat(getComputedStyle(cell).gridTemplateRows.split(' ')[1]);
  return stack && track > 0 ? stack.scrollHeight / track : 0;
})"""


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _HOLD_CASES)
def test_an_upright_hold_uses_the_frame(tmp_path: Path, width: int, height: int, count: int) -> None:
    """The type is as large as the tightest tile allows: in that tile the
    band under the name fills at least 85 % of the height it has (the tile
    less its name and the safe area). Sized more cautiously, every tile of
    the grid would sit in empty space."""
    html, geometry = _hold_html(width, height, count)
    fills = _probe(
        html, width=geometry.canvas_width, height=geometry.canvas_height, tmp_path=tmp_path, js=_FILL_JS
    )
    assert max(fills) >= 0.85, fills


def test_a_reload_costs_its_own_row_and_no_more() -> None:
    """A confirmed reload in one tile of a 2x2 adds that tile's reload row
    to the grid's budget, three figures to a row where that tile has the
    room, so the whole grid shrinks by about one row's height, not two."""
    from splitsmith.overlay_summary_cell import TileRoom, upright_grid_type

    def rooms(reload: bool) -> list[TileRoom]:
        # 1080x1920, 2x2: the right column loses the button column, the
        # bottom row the bottom band; the reload is bottom left.
        return [TileRoom(540, 960), TileRoom(410, 960), TileRoom(540, 710, reload), TileRoom(410, 710)]

    plain = upright_grid_type(540, 960, rooms(False))
    reload = upright_grid_type(540, 960, rooms(True))
    assert reload.reload_columns == 3
    assert reload.scale.headline >= 0.82 * plain.scale.headline, (reload.scale.headline, plain.scale.headline)


_SCALES_JS = r"""() => [...document.querySelectorAll('.anchor-middle-center')].map(
  (stack) => parseFloat(stack.style.getPropertyValue('--fit-scale')) || 1)"""


@pytest.mark.integration
@pytest.mark.parametrize("upright", [True, False])
def test_one_tile_that_needs_less_type_takes_the_whole_upright_grid_with_it(
    tmp_path: Path, upright: bool
) -> None:
    """A tile whose band must shrink to fit its track (here: twelve rows of
    figures) shrinks every tile of an upright grid with it, so the grid
    reads at one scale; a landscape grid keeps fitting each tile alone."""
    from splitsmith.compare.overlay_summary import upright_grid
    from splitsmith.overlay_html import grid_html
    from splitsmith.overlay_layout import Anchor, Element, Flow, Group, Role
    from splitsmith.safe_area import safe_area

    geometry = SpriteGeometry(canvas_width=1080, canvas_height=1920, rows=2, cols=2)
    placements = [
        TilePlacement(label=n, row=i // 2, col=i % 2, present=True) for i, n in enumerate(NAMES[:4])
    ]
    area = safe_area(1080, 1920)
    assert area is not None
    scale = upright_grid(placements, geometry, area).scale

    def tile(label: str, rows: int) -> tuple[Group, ...]:
        figures = tuple(Element(role=Role.HEADLINE, text="0.30", caption="Best") for _ in range(rows * 2))
        return (
            Group(
                anchor=Anchor.TOP_CENTER, flow=Flow.ROW, elements=(Element(role=Role.IDENTITY, text=label),)
            ),
            Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.GRID, elements=figures, columns=2, align="left"),
        )

    cells = [(p, tile(p.label, 12 if i == 0 else 2)) for i, p in enumerate(placements)]
    html = grid_html(
        cells,
        geometry=geometry,
        scale=scale,
        theme=THEME,
        fit_columns=True,
        upright=area if upright else None,
    )
    scales = _probe(html, width=1080, height=1920, tmp_path=tmp_path, js=_SCALES_JS)
    assert scales[0] < 1
    if upright:
        assert len(set(scales)) == 1, scales
    else:
        assert scales[1:] == [1, 1, 1]


def _size(items: list[dict], text: str) -> float:
    return max(item["size"] for item in items if item["text"] == text)


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _SAFE_CASES)
def test_an_upright_match_summary_keeps_every_figure_in_its_tile_and_out_of_the_safe_area(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    items, strip_h = _match_items(tmp_path, width, height, count)
    assert _misplaced(items, frame_width=width, frame_height=height, top=strip_h) == []
    assert min(item["size"] for item in items) >= MIN_FONT_SIZE


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _HOLD_CASES)
def test_an_upright_match_summary_draws_every_tile_at_one_scale(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    items, _ = _match_items(tmp_path, width, height, count)
    sizes = _one_scale(items)
    for role in ("role-headline", "role-detail", "role-label"):
        assert len(sizes.get(role, {0})) == 1, (role, sizes.get(role))


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "count"), _HOLD_CASES)
def test_an_upright_match_summary_lists_its_splits_as_caption_value_rows_first(
    tmp_path: Path, width: int, height: int, count: int
) -> None:
    """ "Best draw" on one line, left of its figure and on the figure's row;
    the Splits band above the Scoring band."""
    items, _ = _match_items(tmp_path, width, height, count)
    checked = 0
    for tile in range(count):
        own = [item for item in items if item["tile"] == tile]
        best = [i for i in own if i["text"] == "Best draw"]
        if not best:
            continue
        (best,) = best
        draw = next(i for i in own if i["text"] == "0.50")
        assert best["lines"] == 1
        assert best["box"]["right"] < draw["box"]["left"]
        assert best["box"]["top"] < draw["box"]["bottom"] and draw["box"]["top"] < best["box"]["bottom"]
        scoring = [i["box"]["top"] for i in own if i["text"] == "Scoring"]
        assert scoring and best["box"]["top"] < min(scoring), tile
        checked += 1
    assert checked >= count - 1


_STRIP_JS = r"""() => {
  const title = document.querySelector('.title');
  const range = document.createRange();
  range.selectNodeContents(title);
  const rects = [...range.getClientRects()];
  const lineTops = new Set(rects.map(r => Math.round(r.top)));
  const box = title.getBoundingClientRect();
  const text = range.getBoundingClientRect();
  return {lines: lineTops.size, size: parseFloat(getComputedStyle(title).fontSize),
          clipped: text.bottom > box.bottom + 0.5 || text.right > box.right + 0.5,
          bottom: text.bottom, height: document.body.clientHeight};
}"""


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
@pytest.mark.parametrize(
    ("title", "lines"),
    [
        ("Bromma Classifier", 1),
        ("Stockholm Open 2026", 1),
        ("Nordic Handgun Championship 2026 Level III presented by a sponsor", 2),
    ],
)
def test_an_upright_match_summary_title_is_never_cut_short(
    tmp_path: Path, width: int, height: int, title: str, lines: int
) -> None:
    *_, strip, strip_h = _match_html(width, height, 9, title=title)
    assert ">Match summary<" in strip
    result = _probe(strip, width=width, height=strip_h, tmp_path=tmp_path, js=_STRIP_JS)
    assert result["lines"] == lines
    assert not result["clipped"]
    assert result["bottom"] <= result["height"] + 0.5
    assert result["size"] >= MIN_FONT_SIZE
    if lines == 1:
        # Big enough to read as the card's title: over a third of the strip.
        assert result["size"] >= strip_h / 3
