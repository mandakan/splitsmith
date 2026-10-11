"""Logos on cards and summaries (issues #1431, #1432, #1433): a wide brand
mark never touches the event logo or any text on an upright card, the match
name takes two lines there rather than end in "...", a summary's text keeps
clear of the shooter's pasted logo, and no logo is drawn larger than its own
pixels.

The logos are generated at the pixel sizes of the ones these issues were
found with on a real match (the event logo 248x300, a ~5:1 wordmark brand
1119x212, a club badge 386x386), opaque to their edges so the picture is the
image. The Chromium tests load the shipped templates and documents as the
renderers do and measure the laid-out page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from splitsmith import composition
from splitsmith.identity import ResolvedIdentity
from splitsmith.logo_spots import LOGO_MARGIN, SUMMARY_LOGO_HEIGHT, logo_box, paste_logo, watermark_image
from splitsmith.match_project import StageScorecard
from splitsmith.match_summary import build_match_summary, match_summary_html
from splitsmith.overlay_theme import load_theme
from splitsmith.stage_summary_data import TileShot, TileStageData

THEME = load_theme("splitsmith")
UPRIGHT = [(1080, 1920), (720, 1280)]


def _logo(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> Path:
    image = Image.new("RGBA", size, (*colour, 255))
    ImageDraw.Draw(image).rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(255, 255, 255, 255), width=3)
    image.save(path)
    return path


@pytest.fixture
def logos(tmp_path: Path) -> dict[str, Path]:
    return {
        "event": _logo(tmp_path / "event.png", (248, 300), (200, 120, 20)),
        "brand": _logo(tmp_path / "brand.png", (1119, 212), (230, 230, 230)),
        "club": _logo(tmp_path / "club.png", (386, 386), (20, 140, 40)),
        "small": _logo(tmp_path / "small.png", (40, 40), (0, 200, 0)),
    }


# --- the paste (no browser) ---------------------------------------------------------


def _drawn(image: Image.Image, colour: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    """The bounding box of the pixels exactly ``colour`` or the logo's white
    outline, on a black canvas."""
    mask = Image.new("L", image.size, 0)
    pixels, out = image.load(), mask.load()
    for y in range(image.height):
        for x in range(image.width):
            if pixels[x, y] in (colour, (255, 255, 255)):
                out[x, y] = 255
    return mask.getbbox()


def test_a_small_logo_is_pasted_at_its_own_size_centred_in_its_slot(logos: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1600, 900), (0, 0, 0))
    out = paste_logo(canvas, logos["small"])
    slot = round(900 * SUMMARY_LOGO_HEIGHT)
    margin = round(900 * LOGO_MARGIN)
    box = _drawn(out, (0, 200, 0))
    assert box is not None
    left, top, right, bottom = box
    assert (right - left, bottom - top) == (40, 40), "a 40 px logo is never drawn larger"
    # Centred in the slot the cards' corner keeps.
    assert left == 1600 - margin - slot + (slot - 40) // 2
    assert top == margin + (slot - 40) // 2
    placed = logo_box(logos["small"], box=(0, 0, 1600, 900))
    assert placed is not None and (placed.x, placed.y, placed.right, placed.bottom) == box


def test_a_logo_larger_than_its_slot_still_fills_it(logos: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1600, 900), (0, 0, 0))
    out = paste_logo(canvas, logos["club"])
    side = round(900 * SUMMARY_LOGO_HEIGHT)
    placed = logo_box(logos["club"], box=(0, 0, 1600, 900))
    assert placed is not None and (placed.width, placed.height) == (side, side)
    assert out.getpixel((placed.x + side // 2, placed.y + side // 2)) == (20, 140, 40)


def test_a_watermark_never_upscales(logos: dict[str, Path]) -> None:
    mark = watermark_image(logos["small"], height=200)
    assert mark is not None and mark.size == (40, 40)
    big = watermark_image(logos["club"], height=100)
    assert big is not None and big.size == (100, 100)


# --- the summaries' declared room ---------------------------------------------------


def _shots(*splits: float) -> tuple[TileShot, ...]:
    out, t = [], 0.0
    for i, split in enumerate(splits):
        t += split
        out.append(TileShot(time_from_beep=t, split=split, interval_class="draw" if i == 0 else "split"))
    return tuple(out)


CARD = StageScorecard(hit_factor=6.1234, stage_pct=88.4, alphas=23, charlies=4, deltas=1, misses=1)
LONG_NAME = "Kristoffer Lindqvist-Bergstrom"
TILE = TileStageData(
    label=LONG_NAME,
    stage_number=3,
    shots=_shots(1.31, 0.22, 0.24, 0.27, 1.05, 0.31),
    stage_time_seconds=9.81,
    scorecard=CARD,
)


def _summary(label: str = LONG_NAME, title: str = "Stockholm Open IPSC Handgun Championship 2026"):
    rows = [
        (
            name,
            TileStageData(
                label=label, stage_number=i + 1, shots=TILE.shots, stage_time_seconds=12.3 + i, scorecard=CARD
            ),
        )
        for i, name in enumerate(["Up the hill", "Long range"])
    ]
    return build_match_summary(rows, title=title, label=label)


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), *UPRIGHT])
def test_a_summary_without_a_logo_is_the_document_it_was(width: int, height: int) -> None:
    from splitsmith.overlay_summary_cell import summary_still_html

    summary = _summary()
    assert match_summary_html(summary, width=width, height=height, theme=THEME, logo=None) == (
        match_summary_html(summary, width=width, height=height, theme=THEME)
    )
    assert "logo-room" not in match_summary_html(summary, width=width, height=height, theme=THEME)
    html = summary_still_html(TILE, LONG_NAME, width=width, height=height, theme=THEME)
    # Neither the rule nor a cell's room (fit.js, inlined, only reads one).
    assert "padding-right: var(--logo-room" not in html
    assert "--logo-room:" not in html[html.index("<body>") :]


# --- in the browser -----------------------------------------------------------------

#: Every drawn text run (clipped by any clipping ancestor, as the summary
#: tests measure it), whether a value was cut with "...", and every image's
#: drawn picture against its own pixels.
_PROBE_JS = r"""() => {
  function contentRect(img) {
    const box = img.getBoundingClientRect();
    const nw = img.naturalWidth, nh = img.naturalHeight;
    if (!(nw > 0 && nh > 0)) return {left: box.left, top: box.top, right: box.right, bottom: box.bottom};
    let s = Math.min(box.width / nw, box.height / nh);
    if (getComputedStyle(img).objectFit === 'scale-down') s = Math.min(s, 1);
    const w = nw * s, h = nh * s;
    const left = box.left + (box.width - w) / 2, top = box.top + (box.height - h) / 2;
    return {left, top, right: left + w, bottom: top + h, scale: s, cls: img.className,
            parent: img.parentElement.className};
  }
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim();
    if (!text) continue;
    const el = node.parentElement;
    if (getComputedStyle(el).display === 'none') continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const r = range.getBoundingClientRect();
    if (r.width === 0) continue;
    let box = {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    let cut = false;
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.textOverflow === 'ellipsis' && a.scrollWidth > a.clientWidth + 0.5) cut = true;
      if (s.overflow !== 'visible') {
        const c = a.getBoundingClientRect();
        box = {left: Math.max(box.left, c.left), right: Math.min(box.right, c.right),
               top: Math.max(box.top, c.top), bottom: Math.min(box.bottom, c.bottom)};
      }
    }
    texts.push({text, box, cut, full: r.right - r.left});
  }
  const images = Array.from(document.querySelectorAll('img')).map(contentRect);
  return {texts, images};
}"""


def _overlaps(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        a["left"] < b["right"]
        and b["left"] < a["right"]
        and a["top"] < b["bottom"]
        and b["top"] < a["bottom"]
    )


def _card_page(
    slot: str, variant: str, width: int, height: int, logos: dict[str, Path], *, info: tuple[str, ...]
) -> dict[str, Any]:
    from splitsmith.look_brand import brand_json
    from splitsmith.looks import load_look, template_for
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.overlay_theme import theme_for

    look = load_look("splitsmith")
    card = composition.MatchTitle(
        text="HFO Masters 2026",
        info=info,
        variant=variant,
        logo=logos["event"],
        credit=slot == "closing",
        brand=composition.BrandMark(logo_path=logos["brand"]),
    )
    context = card_context(
        card,
        slot=slot,  # type: ignore[arg-type]
        width=width,
        height=height,
        fps=30.0,
        theme=theme_for(look),
        shooters=(ResolvedIdentity(label="Mathias Axell", accent=None, logo_path=logos["club"], club=None),),
        brand=brand_json(look, slot, card.brand),  # type: ignore[arg-type]
    )
    template = template_for(look, slot, variant)  # type: ignore[arg-type]
    try:
        with ChromiumRasterizer() as rasterizer:
            view = rasterizer._open_template(template, context=context, width=width, height=height)
            try:
                view.call("seek", float(view.call("poster") or 0))
                view.call("fonts")
                view.call("fit")
                assert view.errors == []
                return view.page.evaluate(_PROBE_JS)
            finally:
                view.close()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), UPRIGHT)
@pytest.mark.parametrize(
    ("slot", "variant"), [("title_page", "default"), ("closing", "default"), ("closing", "rise")]
)
def test_an_upright_card_keeps_its_logos_and_text_apart(
    slot: str, variant: str, width: int, height: int, logos: dict[str, Path]
) -> None:
    info = ("2026-09-13", "Mathias Axell", "Production Optics") if slot == "title_page" else ("2026-09-13",)
    page = _card_page(slot, variant, width, height, logos, info=info)
    images, texts = page["images"], page["texts"]
    assert len(images) == 3, images
    for i, a in enumerate(images):
        for b in images[i + 1 :]:
            assert not _overlaps(a, b), f"{a['cls'] or a['parent']} touches {b['cls'] or b['parent']}"
        for t in texts:
            assert not _overlaps(a, t["box"]), f"{a['cls'] or a['parent']} covers {t['text']!r}"
    names = [t for t in texts if "Masters" in t["text"] or t["text"] == "2026"]
    assert names, texts
    assert not any(t["cut"] for t in texts), [t["text"] for t in texts if t["cut"]]
    # The whole name is drawn: nothing of it is clipped away.
    for t in names:
        assert t["box"]["right"] - t["box"]["left"] >= t["full"] - 0.5, t["text"]
    assert all(0 <= t["box"]["left"] and t["box"]["right"] <= width for t in texts)


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), [(1920, 1080), *UPRIGHT])
@pytest.mark.parametrize(
    ("slot", "variant"), [("title_page", "default"), ("closing", "default"), ("closing", "end-screen")]
)
def test_no_card_draws_a_logo_past_its_own_pixels(
    slot: str, variant: str, width: int, height: int, logos: dict[str, Path]
) -> None:
    page = _card_page(slot, variant, width, height, logos, info=("2026-09-13",))
    assert page["images"]
    for image in page["images"]:
        assert image["scale"] <= 1.0 + 1e-9, image


def _document_page(html: str, width: int, height: int, tmp_path: Path) -> dict[str, Any]:
    from splitsmith.overlay_layout import MIN_FONT_SIZE
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    page_path = tmp_path / "doc.html"
    page_path.write_text(html, encoding="utf-8")
    try:
        with ChromiumRasterizer() as rasterizer:
            context = rasterizer._live_browser().new_context(viewport={"width": width, "height": height})
            try:
                page = context.new_page()
                page.goto(page_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.evaluate(f"window.__splitsmithMinFont = window.__splitsmithMinFont || {MIN_FONT_SIZE}")
                page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                return page.evaluate(_PROBE_JS)
            finally:
                context.close()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


def _clear_of(texts: list[dict[str, Any]], box: Any) -> list[str]:
    rect = {"left": box.x, "top": box.y, "right": box.right, "bottom": box.bottom}
    return [t["text"] for t in texts if _overlaps(t["box"], rect)]


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), [(1920, 1080), *UPRIGHT])
def test_the_match_summary_keeps_its_text_off_the_shooter_logo(
    width: int, height: int, logos: dict[str, Path], tmp_path: Path
) -> None:
    box = logo_box(logos["club"], box=(0, 0, width, height))
    assert box is not None
    html = match_summary_html(_summary(), width=width, height=height, theme=THEME, logo=box)
    page = _document_page(html, width, height, tmp_path)
    assert any(LONG_NAME in t["text"] for t in page["texts"])
    assert _clear_of(page["texts"], box) == []


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), [(1920, 1080), *UPRIGHT])
def test_the_stage_summary_keeps_the_name_off_the_shooter_logo(
    width: int, height: int, logos: dict[str, Path], tmp_path: Path
) -> None:
    from splitsmith.overlay_summary_cell import summary_still_html

    box = logo_box(logos["club"], box=(0, 0, width, height))
    assert box is not None
    html = summary_still_html(TILE, LONG_NAME, width=width, height=height, theme=THEME, logo=box)
    page = _document_page(html, width, height, tmp_path)
    assert any(LONG_NAME[:8] in t["text"] for t in page["texts"])
    assert _clear_of(page["texts"], box) == []


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height", "rows", "cols"), [(1920, 1080, 1, 2), (1080, 1920, 2, 1)])
def test_a_grid_hold_keeps_each_name_off_its_tile_logo(
    width: int, height: int, rows: int, cols: int, logos: dict[str, Path], tmp_path: Path
) -> None:
    from splitsmith.compare import overlay_summary as summ
    from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement

    names = ["Mathias Axell", LONG_NAME]
    geometry = SpriteGeometry(canvas_width=width, canvas_height=height, rows=rows, cols=cols)
    placements = [
        TilePlacement(label=n, row=i // cols, col=i % cols, present=True) for i, n in enumerate(names)
    ]
    data = {
        n: TileStageData(label=n, stage_number=3, shots=TILE.shots, stage_time_seconds=9.81, scorecard=CARD)
        for n in names
    }

    class _Keep:
        html = ""

        def png(self, html: str, *, width: int, height: int) -> bytes:
            _Keep.html = html
            raise RuntimeError("recording only")

    by_label = dict.fromkeys(names, logos["club"])
    summ.build_hold_still(placements, data, {}, geometry, theme=THEME, rasterizer=_Keep(), logos=by_label)
    page = _document_page(_Keep.html, width, height, tmp_path)
    for placement in placements:
        box = logo_box(logos["club"], box=summ._tile_box(placement, geometry, 0))
        assert box is not None
        assert _clear_of(page["texts"], box) == [], placement.label
    # A name that only just reaches the logo shrinks to end before it
    # (fit.js, --logo-room) rather than losing its end to "...".
    assert [t for t in page["texts"] if t["text"] == "Mathias Axell" and not t["cut"]]
