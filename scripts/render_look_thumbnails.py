"""Render the Look gallery's generic thumbnails (spec 2026-09-15 s2).

One 480x270 PNG per gallery variant, under
``src/splitsmith/ui_static/src/assets/look/``, drawn by the same card
builders the two MP4 renderers use (``overlay_card``,
``overlay_summary_cell``, ``overlay_single``) with placeholder text over
a backdrop this script paints itself, so the tiles need neither footage
nor ffmpeg. They need a Chromium the rasterizer can launch, once, here;
the gallery serves the committed files and never rasterizes anything.

Re-run after a card's design changes and commit the result::

    uv run python scripts/render_look_thumbnails.py

``tests/test_render_look_thumbnails.py`` runs ``build_thumbnails`` with a
stub rasterizer and pins the file set against the TS registry.
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from splitsmith import composition  # noqa: E402
from splitsmith.look_sting import sting_context  # noqa: E402
from splitsmith.looks import (  # noqa: E402
    Look,
    load_look,
    sting_template_for,  # noqa: E402
)
from splitsmith.match_project import StageScorecard  # noqa: E402
from splitsmith.overlay_card import build_card_still, build_lower_third, card_scale  # noqa: E402
from splitsmith.overlay_html import single_html  # noqa: E402
from splitsmith.overlay_raster import ChromiumRasterizer, Rasterizer  # noqa: E402
from splitsmith.overlay_single import OverlayRun, run_groups  # noqa: E402
from splitsmith.overlay_summary_cell import build_summary_still  # noqa: E402
from splitsmith.overlay_theme import OverlayTheme, theme_for  # noqa: E402
from splitsmith.stage_summary_data import TileShot, TileStageData  # noqa: E402

WIDTH = 480
HEIGHT = 270
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/splitsmith/ui_static/src/assets/look"

#: The file set the TS registry (``lib/lookGallery.ts``) references.
THUMBNAILS: tuple[str, ...] = (
    "none.png",
    "title-page.png",
    "closing-card.png",
    "stage-card-slate.png",
    "stage-card-lower-third.png",
    "summary-hold.png",
    "overlay.png",
    "transition-cut.png",
    "transition-static.png",
    "transition-zoom.png",
    "transition-fade.png",
    "transition-fadeblack.png",
    "transition-dissolve.png",
    "transition-slideleft.png",
    "transition-slideright.png",
    "transition-circleopen.png",
    "transition-zoomin.png",
    "transition-hblur.png",
    "transition-smoothleft.png",
    "transition-wipeleft.png",
    "transition-sting-wipe.png",
)
#: Every transition tile: the two FCP effects, the cut, and the xfade kinds
#: the MP4 renderer draws (``composition.XFADE_KINDS``, issue #1244).
TRANSITION_KINDS: tuple[str, ...] = tuple(
    name[len("transition-") : -len(".png")] for name in THUMBNAILS if name.startswith("transition-")
)

MATCH = "Match title"
STAGE = "Stage 03 . Standards"
SHOOTER = "A. Shooter"
SPLITS_MS = (1420, 260, 240, 1180, 250, 270, 980, 230, 260)


def paint_backdrop() -> Image.Image:
    """A range-like scene: a dark sky gradient over a lighter ground band
    and a few target silhouettes. Enough for the blur to have something
    to blur; nothing a viewer would mistake for footage."""
    im = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(im)
    for y in range(HEIGHT):
        t = y / HEIGHT
        sky = (int(38 + 30 * t), int(46 + 34 * t), int(58 + 30 * t))
        draw.line([(0, y), (WIDTH, y)], fill=sky)
    horizon = int(HEIGHT * 0.62)
    draw.rectangle([0, horizon, WIDTH, HEIGHT], fill=(96, 84, 66))
    # Uneven on purpose: the transition tiles roll the scene for their
    # second half, and a repeating scene would hide the seam.
    for x, tall in ((80, 70), (190, 95), (400, 55)):
        draw.rectangle([x - 22, horizon - tall, x + 22, horizon], fill=(180, 140, 92))
        draw.rectangle([x - 12, horizon - tall - 30, x + 12, horizon - tall], fill=(180, 140, 92))
    return im


def _sample_tile() -> TileStageData:
    shots: list[TileShot] = []
    t = 0.0
    for i, ms in enumerate(SPLITS_MS):
        t += ms / 1000
        shots.append(TileShot(time_from_beep=t, split=ms / 1000 if i else t))
    return TileStageData(
        label=SHOOTER,
        stage_number=3,
        shots=tuple(shots),
        stage_time_seconds=18.42,
        scorecard=StageScorecard(hit_factor=6.21, alphas=14, charlies=3, deltas=1, misses=0),
    )


def _overlay(backdrop: Image.Image, *, rasterizer: Rasterizer, theme: OverlayTheme) -> Image.Image:
    run = OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=18, last_split=0.26)
    html = single_html(run_groups(run), width=WIDTH, height=HEIGHT, scale=card_scale(HEIGHT), theme=theme)
    png = rasterizer.png(html, width=WIDTH, height=HEIGHT)
    out = backdrop.convert("RGBA")
    with Image.open(io.BytesIO(png)) as text:
        out.alpha_composite(text.convert("RGBA"))
    return out.convert("RGB")


def _xfade_mid_frame(kind: str, left: Image.Image, right: Image.Image) -> Image.Image:
    """The xfade ``kind`` halfway through, drawn in PIL so the gallery's
    tile shows what the boundary segment does without an ffmpeg run at
    authoring time. Each kind reads differently at a glance; the real
    frames come from ``scripts/render_match_frames.py --transition``."""
    w, h = left.size
    if kind in ("fade", "dissolve"):
        out = Image.blend(left, right, 0.5)
        if kind == "dissolve":
            noise = Image.effect_noise((w, h), 96).point(lambda v: 255 if v > 128 else 0).convert("L")
            out = Image.composite(right, left, noise)
        return out
    if kind == "fadeblack":
        return Image.blend(left, Image.new("RGB", (w, h), (0, 0, 0)), 0.8)
    if kind in ("slideleft", "slideright", "wipeleft", "smoothleft"):
        out = left.copy()
        if kind == "slideleft":
            out.paste(right.crop((0, 0, w // 2, h)), (w // 2, 0))
            out.paste(left.crop((w // 2, 0, w, h)), (0, 0))
        elif kind == "slideright":
            out.paste(right.crop((w // 2, 0, w, h)), (0, 0))
            out.paste(left.crop((0, 0, w // 2, h)), (w // 2, 0))
        elif kind == "wipeleft":  # the wipe edge past the middle: not the cut's seam
            edge = int(w * 0.62)
            out.paste(right.crop((edge, 0, w, h)), (edge, 0))
        else:  # smoothleft: a soft edge
            ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))
            out = Image.composite(left, right, ramp)
        return out
    if kind == "circleopen":
        mask = Image.new("L", (w, h), 0)
        r = min(w, h) // 3
        ImageDraw.Draw(mask).ellipse((w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r), fill=255)
        return Image.composite(right, left, mask.filter(ImageFilter.GaussianBlur(2)))
    if kind == "zoomin":
        bw, bh = int(w * 1.3), int(h * 1.3)
        x0, y0 = (bw - w) // 2, (bh - h) // 2
        zoomed = left.resize((bw, bh)).crop((x0, y0, x0 + w, y0 + h))
        return Image.blend(zoomed, right, 0.5)
    if kind == "hblur":
        return Image.blend(
            left.filter(ImageFilter.BoxBlur((12, 0))), right.filter(ImageFilter.BoxBlur((12, 0))), 0.5
        )
    raise ValueError(f"no thumbnail recipe for transition kind {kind!r}")


def _sting_frame(name: str, base: Image.Image, *, rasterizer: Rasterizer, look: Look) -> Image.Image:
    """The sting ``name`` at its poster (the band on the seam) over the
    mid-fade frame it rides, drawn by the Look's own template."""
    template = sting_template_for(look, name)
    if template is None:
        raise RuntimeError(f"the {look.name} Look has no sting {name!r}")
    context = sting_context(
        kind=f"sting:{name}",
        seconds=1.0,
        from_label=STAGE,
        to_label="Stage 4",
        width=WIDTH,
        height=HEIGHT,
        fps=30.0,
        theme=theme_for(look),
        shooters=(),
    )
    png = rasterizer.render_template(template, context=context, width=WIDTH, height=HEIGHT)
    out = base.convert("RGBA")
    with Image.open(io.BytesIO(png)) as band:
        out.alpha_composite(band.convert("RGBA"))
    return out


def _transition(
    kind: str, backdrop: Image.Image, *, rasterizer: Rasterizer | None = None, look: Look | None = None
) -> Image.Image:
    """Two half-frames with the transition drawn on the seam: a hard edge,
    a desaturated held band, or a zoom-blurred band for the FCP effects; a
    mid-fade frame of the whole tile for an xfade kind; the Look's sting at
    its poster over that mid-fade for ``sting-<name>`` (#1245)."""
    left = backdrop
    # The "next stage": the same scene rolled sideways, so the seam is a
    # real discontinuity (a mirror image would be continuous at it).
    right = ImageChops.offset(backdrop, 150, 0)
    if kind.startswith("sting-"):
        if rasterizer is None or look is None:
            raise RuntimeError("a sting tile needs the rasterizer and the Look")
        return _sting_frame(
            kind[len("sting-") :], _xfade_mid_frame("fade", left, right), rasterizer=rasterizer, look=look
        )
    if kind not in ("cut", "static", "zoom"):
        return _xfade_mid_frame(kind, left, right)
    out = Image.new("RGB", (WIDTH, HEIGHT))
    half = WIDTH // 2
    out.paste(left.crop((0, 0, half, HEIGHT)), (0, 0))
    out.paste(right.crop((half, 0, WIDTH, HEIGHT)), (half, 0))
    band = (half - 60, 0, half + 60, HEIGHT)
    if kind == "static":
        strip = left.crop(band).convert("L").convert("RGB")
        out.paste(strip, band[:2])
    elif kind == "zoom":
        strip = left.crop(band)
        w, h = strip.size
        blurred = strip
        for scale in (1.03, 1.06, 1.09):
            bw, bh = int(w * scale), int(h * scale)
            x0, y0 = (bw - w) // 2, (bh - h) // 2
            bigger = strip.resize((bw, bh)).crop((x0, y0, x0 + w, y0 + h))
            blurred = Image.blend(blurred, bigger, 0.5)
        out.paste(blurred.filter(ImageFilter.GaussianBlur(2)), band[:2])
    return out


def build_thumbnails(out: Path, *, rasterizer: Rasterizer, look: Look) -> list[Path]:
    theme = theme_for(look)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    plain = paint_backdrop()
    with tempfile.TemporaryDirectory() as tmp_name:
        backdrop_png = Path(tmp_name) / "backdrop.png"
        plain.save(backdrop_png)

        def save(name: str, image: Image.Image | None) -> None:
            if image is None:
                raise RuntimeError(f"{name}: the card did not compose")
            path = out / name
            image.convert("RGB").save(path, optimize=True)
            written.append(path)

        card = {
            "width": WIDTH,
            "height": HEIGHT,
            "fps": 30.0,
            "look": look,
            "rasterizer": rasterizer,
            "backdrop": backdrop_png,
        }
        save("none.png", plain)
        title = composition.MatchTitle(text=MATCH, info=("2026-06-27", "Production Optics"))
        save("title-page.png", build_card_still(title, slot="title_page", **card))
        closing = composition.MatchTitle(text=MATCH, info=("2026-06-27",))
        save("closing-card.png", build_card_still(closing, slot="closing", **card))
        slate = composition.TitleCard(text=STAGE, duration_seconds=1.5, style="slate", info=("24 rounds",))
        save("stage-card-slate.png", build_card_still(slate, slot="slate", **card))
        lower = composition.TitleCard(
            text=STAGE, duration_seconds=1.5, style="lower-third", info=("24 rounds",)
        )
        third = build_lower_third(
            lower, width=WIDTH, height=HEIGHT, fps=30.0, look=look, rasterizer=rasterizer
        )
        if third is None:
            raise RuntimeError("lower third did not compose")
        over = plain.convert("RGBA")
        over.alpha_composite(third)
        save("stage-card-lower-third.png", over)
        summary = build_summary_still(
            _sample_tile(),
            SHOOTER,
            width=WIDTH,
            height=HEIGHT,
            theme=theme,
            rasterizer=rasterizer,
            backdrop=backdrop_png,
        )
        save("summary-hold.png", summary)
        save("overlay.png", _overlay(plain, rasterizer=rasterizer, theme=theme))
        for kind in TRANSITION_KINDS:
            save(f"transition-{kind}.png", _transition(kind, plain, rasterizer=rasterizer, look=look))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", default="splitsmith", help="an installed Look name")
    args = parser.parse_args()
    with ChromiumRasterizer() as rasterizer:
        written = build_thumbnails(args.out, rasterizer=rasterizer, look=load_look(args.theme))
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
