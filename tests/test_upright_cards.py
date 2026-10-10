"""Upright single-shooter cards (issue #1394): the predicate, the platform
safe area, and the match summary and stage summary laid out for a canvas
taller than wide.

The Chromium tests take the HTML the product's own ``build_*_still`` hands
its rasterizer and measure it laid out in the real browser, fit script run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.events import ReloadFigure
from splitsmith.match_project import StageScorecard
from splitsmith.match_summary import build_match_summary, build_match_summary_still, match_summary_html
from splitsmith.overlay_layout import MIN_FONT_SIZE
from splitsmith.overlay_summary_cell import build_summary_still
from splitsmith.overlay_theme import load_theme
from splitsmith.safe_area import is_upright, safe_area
from splitsmith.stage_summary_data import TileShot, TileStageData

THEME = load_theme("splitsmith")
CARD = StageScorecard(
    hit_factor=6.1234, stage_pct=88.4, alphas=23, charlies=4, deltas=1, misses=1, no_shoots=0, procedurals=0
)
UPRIGHT = [(1080, 1920), (720, 1280)]
NAMES = ["Up the hill", "Long range with a very long stage name indeed", "Short", "Classifier CM 99-11"]


class _Recording:
    """A rasterizer that keeps the HTML it is handed and draws nothing."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append(html)
        raise RuntimeError("recording only")


def _shots(*splits: float, moving: tuple[int, ...] = ()) -> tuple[TileShot, ...]:
    out, t = [], 0.0
    for i, split in enumerate(splits):
        t += split
        out.append(
            TileShot(
                time_from_beep=t,
                split=split,
                interval_class="draw" if i == 0 else ("movement" if i == 4 else "split"),
                moving=i in moving,
            )
        )
    return tuple(out)


def _tile(kind: str) -> TileStageData:
    splits = (1.31, 0.22, 0.24, 0.27, 1.05, 0.31, 0.29, 0.33, 0.28, 0.25, 0.23)
    shots = _shots(*splits, moving=(4, 5, 6, 7) if kind == "regions" else ())
    reloads = (
        (ReloadFigure(event_id="r1", duration=1.42, moving=False, exposed=1.42),) if kind != "plain" else ()
    )
    return TileStageData(
        label="Mathias Axell",
        stage_number=3,
        shots=() if kind == "bare" else shots,
        stage_time_seconds=9.81,
        scorecard=CARD,
        reloads=reloads,
    )


def _match(stages: int, *, card: StageScorecard = CARD):
    return build_match_summary(
        [
            (
                NAMES[i % 4],
                TileStageData(
                    label="Mathias",
                    stage_number=i + 1,
                    shots=_shots(1.234, 0.21, 0.31, 0.27, 0.24, 0.19, 0.33, 0.26),
                    stage_time_seconds=14.21 + i,
                    scorecard=card,
                ),
            )
            for i in range(stages)
        ],
        title="Stockholm Open Championship 2026",
        label="Mathias Axell",
    )


def _stage_html(kind: str, width: int, height: int, label: str = "Mathias Axell") -> str:
    fake = _Recording()
    build_summary_still(
        _tile(kind), label, width=width, height=height, theme=THEME, rasterizer=fake, backdrop=None
    )
    (html,) = fake.calls
    return html


def _match_html(summary, width: int, height: int) -> str:
    fake = _Recording()
    build_match_summary_still(
        summary, width=width, height=height, theme=THEME, rasterizer=fake, backdrop=None
    )
    (html,) = fake.calls
    return html


# --- the predicate and the safe area ------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "upright"),
    [(1080, 1920, True), (720, 1280, True), (1079, 1080, True), (1080, 1080, False), (1920, 1080, False)],
)
def test_upright_means_taller_than_wide(width: int, height: int, upright: bool) -> None:
    assert is_upright(width, height) is upright


def test_the_safe_area_is_the_bottom_band_and_the_button_column() -> None:
    area = safe_area(1080, 1920)
    assert area is not None
    # 13 % of the height along the bottom, the right 12 % from 40 % down.
    assert (area.bottom, area.right, area.right_top) == (250, 130, 768)
    assert (area.bottom_line, area.right_line) == (1670, 950)
    assert area.covers(65, 1600, 400, 1680)  # into the bottom band
    assert area.covers(900, 1000, 960, 1040)  # into the button column
    assert not area.covers(900, 200, 1000, 300)  # beside the title, above the column
    assert not area.covers(65, 1000, 949, 1660)
    assert "--safe-bottom: 250px" in area.css_vars()
    small = safe_area(720, 1280)
    assert small is not None and (small.bottom, small.right, small.right_top) == (166, 86, 512)


@pytest.mark.parametrize(("width", "height"), [(1080, 1080), (1920, 1080), (1440, 1080)])
def test_square_and_wider_canvases_have_no_safe_area(width: int, height: int) -> None:
    assert safe_area(width, height) is None
    assert "--safe-bottom" not in match_summary_html(_match(12), width=width, height=height, theme=THEME)
    assert "--safe-bottom" not in _stage_html("regions", width, height)


# --- the declared layout ------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_an_upright_match_summary_leads_its_table_with_the_splits(width: int, height: int) -> None:
    html = _match_html(_match(12), width, height)
    head = html[html.index("<thead>") : html.index("</thead>")]
    order = [head.index(f">{h}<") for h in ("Stage", "Draw", "Split", "Time", "HF", "%")]
    assert order == sorted(order)
    assert html.count("<table>") + html.count("<table ") == 1  # one column of rows, never two
    assert "Match summary" in html and "-webkit-line-clamp: 2" in html


@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_an_upright_stage_summary_leads_with_the_splits(width: int, height: int) -> None:
    for kind in ("plain", "regions", "reload"):
        html = _stage_html(kind, width, height)
        assert html.index(">Splits<") < html.index(">Scoring<"), kind
    regions = _stage_html("regions", width, height)
    assert ">Static<" in regions and ">Moving<" in regions


# --- in the browser -----------------------------------------------------------------

_PROBE_JS = r"""() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
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
    // What is drawn: the text's box clipped by any ancestor that clips
    // (a name cut with an ellipsis draws up to its cell's edge).
    let box = {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      if (getComputedStyle(a).overflow !== 'visible') {
        const c = a.getBoundingClientRect();
        box = {left: Math.max(box.left, c.left), right: Math.min(box.right, c.right),
               top: Math.max(box.top, c.top), bottom: Math.min(box.bottom, c.bottom)};
      }
    }
    out.push({text, size: parseFloat(getComputedStyle(el).fontSize), box});
  }
  return out;
}"""


def _probe(html: str, *, width: int, height: int, tmp_path: Path) -> list[dict]:
    """Lay ``html`` out as ``ChromiumRasterizer.png`` does (file URL, fonts
    ready, the fit script run) and return every drawn text run."""
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    page_path = tmp_path / "card.html"
    page_path.write_text(html, encoding="utf-8")
    try:
        with ChromiumRasterizer() as rasterizer:
            context = rasterizer._live_browser().new_context(viewport={"width": width, "height": height})
            try:
                page = context.new_page()
                page.goto(page_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                return page.evaluate(_PROBE_JS)
            finally:
                context.close()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


def _misplaced(items: list[dict], width: int, height: int) -> list[str]:
    """Text drawn past the frame or into the platform safe area. The
    expected area is written out here, not read from the module under
    test: the bottom 13 % and the right 12 % from 40 % down."""
    bottom_line = height - round(height * 0.13)
    right_line = width - round(width * 0.12)
    right_top = round(height * 0.40)
    out = []
    for item in items:
        b = item["box"]
        if b["right"] <= b["left"]:
            continue
        if b["left"] < -0.5 or b["top"] < -0.5 or b["right"] > width + 0.5 or b["bottom"] > height + 0.5:
            out.append(f"off the frame: {item['text']}")
        elif b["bottom"] > bottom_line + 0.5:
            out.append(f"in the bottom band: {item['text']}")
        elif b["right"] > right_line + 0.5 and b["bottom"] > right_top:
            out.append(f"in the button column: {item['text']}")
    return out


def _top(items: list[dict], text: str) -> float:
    return min(item["box"]["top"] for item in items if item["text"] == text)


def _left(items: list[dict], text: str) -> float:
    return min(item["box"]["left"] for item in items if item["text"] == text)


def _size(items: list[dict], text: str) -> float:
    return max(item["size"] for item in items if item["text"] == text)


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
@pytest.mark.parametrize("stages", [3, 12, 24])
def test_an_upright_match_summary_uses_the_frame_and_keeps_out_of_the_safe_area(
    tmp_path: Path, width: int, height: int, stages: int
) -> None:
    items = _probe(_match_html(_match(stages), width, height), width=width, height=height, tmp_path=tmp_path)
    assert _misplaced(items, width, height) == []
    # Splits lead and are the largest figures on the card: over a tenth of
    # the width each (the landscape-sized card drew them at 0.048).
    assert _size(items, "0.26") >= 0.1 * width
    assert _top(items, "Avg split") < _top(items, "A") < _top(items, "Draw")
    # The table's splits lead: Draw and Split left of Time, HF and %.
    lefts = [_left(items, h) for h in ("Draw", "Split", "Time", "HF", "%")]
    assert lefts == sorted(lefts)
    assert min(item["size"] for item in items) >= MIN_FONT_SIZE
    # A twelve-stage table reaches past mid-frame.
    if stages >= 12:
        assert max(item["box"]["bottom"] for item in items) > 0.75 * height


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_an_upright_match_summary_fits_counts_far_past_a_real_match(
    tmp_path: Path, width: int, height: int
) -> None:
    """999 A on each of twelve stages: the counts row shrinks to fit its
    columns rather than running a figure into the next."""
    card = StageScorecard(
        hit_factor=12.3456,
        stage_pct=100.0,
        alphas=999,
        charlies=999,
        deltas=99,
        misses=99,
        no_shoots=9,
        procedurals=9,
    )
    items = _probe(
        _match_html(_match(12, card=card), width, height), width=width, height=height, tmp_path=tmp_path
    )
    assert _misplaced(items, width, height) == []
    figures = sorted(
        (i for i in items if i["text"] in {"11988", "1188", "108"}), key=lambda i: i["box"]["left"]
    )
    for left, right in zip(figures, figures[1:], strict=False):
        assert left["box"]["right"] < right["box"]["left"], (left["text"], right["text"])


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
@pytest.mark.parametrize("kind", ["plain", "regions", "reload", "bare"])
def test_an_upright_stage_summary_keeps_out_of_the_safe_area_with_splits_first(
    tmp_path: Path, width: int, height: int, kind: str
) -> None:
    items = _probe(_stage_html(kind, width, height), width=width, height=height, tmp_path=tmp_path)
    assert _misplaced(items, width, height) == []
    assert min(item["size"] for item in items) >= MIN_FONT_SIZE
    if kind == "bare":
        assert not any(item["text"] == "Splits" for item in items)
        return
    assert _top(items, "Splits") < _top(items, "Scoring")
    # Hit factor and time never outrank the splits.
    assert _size(items, "6.12") <= _size(items, "0.22")
    if kind == "regions":
        assert _top(items, "Static") < _top(items, "Moving") < _top(items, "Reloads")
    if kind == "plain":
        # Best / Avg over Worst / Draw at about 1.2x the landscape card's
        # 1920x1080 figure (121 px), keyed to the width.
        assert _size(items, "0.22") >= 0.12 * width
        assert _top(items, "Best") == _top(items, "Avg") < _top(items, "Worst") == _top(items, "Draw")


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
def test_an_upright_stage_summary_caps_a_long_name(tmp_path: Path, width: int, height: int) -> None:
    label = "Maximiliana Vanderberg-Lindqvist of the Stockholm Shooting Club"
    items = _probe(
        _stage_html("regions", width, height, label), width=width, height=height, tmp_path=tmp_path
    )
    assert _misplaced(items, width, height) == []
    assert _size(items, label) <= round(0.12 * width)
