"""Render stage-summary cells straight to PNG, without the video pipeline.

The compare grid's summary is HTML and CSS rasterized by Chromium
(:mod:`splitsmith.overlay_html`, :mod:`splitsmith.overlay_raster`), so a
layout question does not need ffmpeg to answer. This writes candidate cell
markup, screenshots it at real cell size, and composites it over a blurred,
dimmed still -- about four seconds for twenty cells, against tens of minutes
for a full ``render_grid_frames.py`` pass including a 4K encode.

That gap is the point. Every overlay defect found on #683 was found by
looking at a rendered cell and measuring it, never by reading code -- and
the expensive way of looking is why several rounds went into building
something before anyone saw it.

``v_bands`` is the design #683 Task 8 shipped, and the docstrings in
``overlay_layout.CellScale`` and ``overlay_summary`` cite this file as the
authority for their numbers. Keep the two in step, or correct those
docstrings.

Run it::

    uv run python scripts/mock_summary_cell.py

Needs the browser Playwright installs (``uv run playwright install chromium
--only-shell``); it never touches ffmpeg.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from PIL import Image, ImageFilter  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from splitsmith.overlay_theme import load_theme  # noqa: E402

OUT = REPO / "build" / "mock-cells"
FONTS = REPO / "src/splitsmith/data/fonts"


def hexc(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


class Shooter:
    """Just enough of a tile to draw one cell."""

    def __init__(
        self,
        name,
        a,
        c,
        d,
        m,
        ns,
        p,
        hf,
        time_s,
        best,
        avg,
        worst,
        draw,
        shots,
        dq=False,
        manual=False,
    ):
        self.name, self.a, self.c, self.d = name, a, c, d
        self.m, self.ns, self.p = m, ns, p
        self.hf, self.time_s, self.dq, self.manual = hf, time_s, dq, manual
        self.best, self.avg, self.worst, self.draw, self.shots = best, avg, worst, draw, shots


ANDERS = Shooter("Anders", 10, 1, 1, 0, 0, 0, 12.00, 4.50, 0.30, 0.90, 2.50, 0.50, 5)
SANNA = Shooter("Sanna", 4, 4, 2, 2, 0, 1, 0.93, 4.30, 0.30, 0.85, 1.90, 0.70, 5)
LONGNAME = Shooter("Mathias Axell-Lindstrom", 10, 1, 1, 0, 0, 0, 12.17, 4.60, 0.30, 0.72, 1.50, 1.20, 5)
DQ = Shooter("Anders", None, None, None, None, None, None, None, 4.00, 2.50, 2.50, 2.50, 0.50, 2, dq=True)
BARE = Shooter("Bea", None, None, None, None, None, None, None, None, None, None, None, None, 0)


def font_face(theme) -> str:
    mono = (FONTS / "JetBrainsMono-Bold.ttf").resolve().as_uri()
    disp = (FONTS / "Antonio-VariableFont.ttf").resolve().as_uri()
    return f"""
    @font-face {{ font-family:"SSMono"; src:url("{mono}") format("truetype"); font-weight:700; }}
    @font-face {{ font-family:"SSDisp"; src:url("{disp}") format("truetype"); font-weight:100 700; }}
    """


def base_css(theme, cell_w: int, cell_h: int) -> str:
    t = theme
    return f"""
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    html,body {{ width:{cell_w}px; height:{cell_h}px; background:transparent; }}
    .cell {{ position:relative; width:{cell_w}px; height:{cell_h}px; overflow:hidden;
             padding:{max(16, cell_h//22)}px {max(18, cell_w//24)}px;
             font-family:"SSMono",monospace; font-weight:700;
             text-shadow:0 {max(1,cell_h//360)}px {max(2,cell_h//180)}px {hexc(t.stroke)};
             -webkit-text-stroke:{max(1.0, cell_h/540):.1f}px {hexc(t.stroke)};
             paint-order:stroke fill; color:{hexc(t.ink)}; }}
    .name {{ font-family:"SSDisp",sans-serif; color:{hexc(t.ink)}; line-height:1; }}
    .rule {{ border:0; border-top:{max(1,cell_h//360)}px solid {hexc(t.rule)}; opacity:.9; }}
    .lbl {{ color:#C9CCD2; letter-spacing:.08em; text-transform:uppercase;
            -webkit-text-stroke:0; text-shadow:0 1px 2px {hexc(t.stroke)}; }}
    .A {{ color:{hexc(t.split_good)}; }}
    .C {{ color:{hexc(t.ink)}; }}
    .D {{ color:{hexc(t.split)}; }}
    .P {{ color:{hexc(t.accent_text)}; }}
    .pl {{ background:{hexc(t.accent_fill)}; color:{hexc(t.ink)}; -webkit-text-stroke:0;
           border-radius:{max(2,cell_h//180)}px; padding:0 .28em; }}
    .dq {{ background:{hexc(t.accent_fill)}; color:{hexc(t.ink)}; -webkit-text-stroke:0;
           border-radius:{max(3,cell_h//140)}px; padding:.08em .42em; }}
    """


def counts_html(s) -> str:
    if s.a is None:
        return ""
    out = []
    for val, cls in ((s.a, "A"), (s.c, "C"), (s.d, "D")):
        out.append(f'<span class="{cls}">{cls}{val}</span>')
    for val, tag in ((s.m, "M"), (s.ns, "NS"), (s.p, "P")):
        cls = "pl" if val else "P"
        out.append(f'<span class="{cls}">{tag}{val}</span>')
    return " ".join(out)


def splits_html(s) -> str:
    if s.best is None:
        return ""
    return "".join(
        f'<div class="srow"><span class="lbl">{k}</span><span class="sv">{v:.2f}</span></div>'
        for k, v in (("Best", s.best), ("Avg", s.avg), ("Worst", s.worst), ("Draw", s.draw))
    )


def time_text(s) -> str:
    if s.time_s is None:
        return ""
    return f"{s.time_s:.2f}s" + (" (manual)" if s.manual else "")


# --- variant 1: two columns, scoring | splits -------------------------


def v_columns(s, theme, cw, ch) -> str:
    name_px, fig_px, lbl_px, cnt_px = ch // 7, ch // 9, max(13, ch // 20), ch // 15
    dq = '<span class="dq">DQ</span>' if s.dq else ""
    hf_html = f'<div class="fig">{s.hf:.2f} <span class="u">HF</span></div>' if s.hf is not None else ""
    body = ""
    if s.a is not None or s.time_s is not None:
        body = f"""
        <div class="cols">
          <div class="col">
            <div class="lbl colhead">Scoring</div>
            <div class="counts">{counts_html(s)}</div>
            <div class="figs">{hf_html}
              <div class="fig">{time_text(s)}</div></div>
          </div>
          <div class="col">
            <div class="lbl colhead">Splits</div>
            {splits_html(s)}
          </div>
        </div>"""
    return f"""<style>{font_face(theme)}{base_css(theme, cw, ch)}
      .cell {{ display:flex; flex-direction:column; gap:{ch//28}px; }}
      .top {{ display:flex; align-items:baseline; gap:.5em; }}
      .name {{ font-size:{name_px}px; }}
      .dq {{ font-size:{int(name_px*.5)}px; }}
      .cols {{ flex:1; display:grid; grid-template-columns:1fr 1fr;
               gap:{cw//16}px; align-content:center; }}
      .col {{ display:flex; flex-direction:column; gap:{ch//34}px; }}
      .colhead {{ font-size:{lbl_px}px; }}
      .counts {{ font-size:{cnt_px}px; display:flex; flex-wrap:wrap; gap:.45em; }}
      .figs {{ display:flex; flex-direction:column; gap:{ch//40}px; }}
      .fig {{ font-size:{fig_px}px; line-height:1; }}
      .u {{ font-size:{int(fig_px*.5)}px; color:{hexc(theme.muted)}; -webkit-text-stroke:0; }}
      .srow {{ display:flex; justify-content:space-between; align-items:baseline; gap:.6em; }}
      .srow .lbl {{ font-size:{lbl_px}px; }}
      .sv {{ font-size:{fig_px}px; line-height:1.05; font-variant-numeric:tabular-nums; }}
    </style>
    <div class="cell"><div class="top"><span class="name">{s.name}</span>{dq}</div>{body}</div>"""


# --- variant 2: stacked bands, scoring over splits --------------------


def v_bands(s, theme, cw, ch) -> str:
    name_px, fig_px, lbl_px, cnt_px = ch // 7, ch // 8, max(13, ch // 20), ch // 14
    dq = '<span class="dq">DQ</span>' if s.dq else ""
    score = ""
    if s.a is not None or s.time_s is not None:
        hf = f'<span class="fig">{s.hf:.2f}<span class="u">HF</span></span>' if s.hf is not None else ""
        score = f"""<div class="band">
            <div class="lbl">Scoring</div>
            <div class="counts">{counts_html(s)}</div>
            <div class="figrow">{hf}<span class="fig">{time_text(s)}</span></div>
          </div>"""
    sp = ""
    if s.best is not None:
        cells = "".join(
            f'<div class="sc"><span class="lbl">{k}</span><span class="sv">{v:.2f}</span></div>'
            for k, v in (("Best", s.best), ("Avg", s.avg), ("Worst", s.worst), ("Draw", s.draw))
        )
        sp = f'<div class="band"><div class="lbl">Splits</div><div class="sgrid">{cells}</div></div>'
    return f"""<style>{font_face(theme)}{base_css(theme, cw, ch)}
      .cell {{ display:flex; flex-direction:column; gap:{ch//30}px; }}
      .top {{ display:flex; align-items:baseline; gap:.5em; }}
      .name {{ font-size:{name_px}px; }}
      .dq {{ font-size:{int(name_px*.5)}px; }}
      .stack {{ flex:1; display:flex; flex-direction:column; justify-content:center; gap:{ch//22}px; }}
      .band {{ display:flex; flex-direction:column; gap:{ch//40}px; }}
      .band .lbl {{ font-size:{lbl_px}px; }}
      .counts {{ font-size:{cnt_px}px; display:flex; flex-wrap:wrap; gap:.5em; }}
      .figrow {{ display:flex; gap:{cw//12}px; }}
      .fig {{ font-size:{fig_px}px; line-height:1; }}
      .u {{ font-size:{int(fig_px*.45)}px; color:{hexc(theme.muted)};
            -webkit-text-stroke:0; margin-left:.25em; }}
      .sgrid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:{cw//24}px; }}
      .sc {{ display:flex; flex-direction:column; gap:{ch//70}px; }}
      .sc .lbl {{ font-size:{lbl_px}px; }}
      .sv {{ font-size:{fig_px}px; line-height:1; font-variant-numeric:tabular-nums; }}
    </style>
    <div class="cell"><div class="top"><span class="name">{s.name}</span>{dq}</div>
      <div class="stack">{score}{sp}</div></div>"""


VARIANTS = {"columns": v_columns, "bands": v_bands}


def backdrop(cw: int, ch: int) -> Image.Image:
    """A real blurred, dimmed freeze frame to composite over."""
    src = REPO / "build/grid-frames/stage2-mid-action.png"
    im = Image.open(src).convert("RGB")
    im = im.crop((0, 0, im.width // 2, im.height // 2)).resize((cw, ch), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(max(8, ch // 60)))
    return Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), 0.45)


def main():
    theme = load_theme("splitsmith")
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = {"4k-cell": (1280, 720), "small-cell": (640, 360)}
    people = {"anders": ANDERS, "sanna": SANNA, "longname": LONGNAME, "dq": DQ, "bare": BARE}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium-headless-shell")
        for sname, (cw, ch) in sizes.items():
            bg = backdrop(cw, ch)
            page = browser.new_page(viewport={"width": cw, "height": ch}, device_scale_factor=1)
            for vname, fn in VARIANTS.items():
                for pname, person in people.items():
                    html = fn(person, theme, cw, ch)
                    f = OUT / f"_{vname}-{pname}-{sname}.html"
                    f.write_text(html)
                    page.goto(f.as_uri())
                    page.evaluate("document.fonts.ready")
                    shot = page.screenshot(omit_background=True, type="png")
                    over = Image.open(__import__("io").BytesIO(shot)).convert("RGBA")
                    out = bg.convert("RGBA")
                    out.alpha_composite(over)
                    out.convert("RGB").save(OUT / f"{vname}-{pname}-{sname}.png")
            page.close()
        browser.close()
    for f in OUT.glob("_*.html"):
        f.unlink()
    print(f"{len(list(OUT.glob('*.png')))} cells in {OUT}")


if __name__ == "__main__":
    main()
