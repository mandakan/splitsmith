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
from splitsmith.match_project import StageScorecard  # noqa: E402
from splitsmith.overlay_card import build_card_still, build_lower_third, card_scale  # noqa: E402
from splitsmith.overlay_html import single_html  # noqa: E402
from splitsmith.overlay_raster import ChromiumRasterizer, Rasterizer  # noqa: E402
from splitsmith.overlay_single import OverlayRun, run_groups  # noqa: E402
from splitsmith.overlay_summary_cell import build_summary_still  # noqa: E402
from splitsmith.overlay_theme import OverlayTheme, load_theme  # noqa: E402
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


def _transition(kind: str, backdrop: Image.Image) -> Image.Image:
    """Two half-frames with the transition drawn on the seam: a hard edge,
    a desaturated held band, or a zoom-blurred band."""
    left = backdrop
    # The "next stage": the same scene rolled sideways, so the seam is a
    # real discontinuity (a mirror image would be continuous at it).
    right = ImageChops.offset(backdrop, 150, 0)
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


def build_thumbnails(out: Path, *, rasterizer: Rasterizer, theme: OverlayTheme) -> list[Path]:
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
            "theme": theme,
            "rasterizer": rasterizer,
            "backdrop": backdrop_png,
        }
        save("none.png", plain)
        title = composition.MatchTitle(text=MATCH, info=("2026-06-27", "Production Optics"))
        save("title-page.png", build_card_still(title, **card))
        closing = composition.MatchTitle(text=MATCH, info=("2026-06-27",))
        save("closing-card.png", build_card_still(closing, **card))
        slate = composition.TitleCard(text=STAGE, duration_seconds=1.5, style="slate", info=("24 rounds",))
        save("stage-card-slate.png", build_card_still(slate, **card))
        lower = composition.TitleCard(
            text=STAGE, duration_seconds=1.5, style="lower-third", info=("24 rounds",)
        )
        third = build_lower_third(lower, width=WIDTH, height=HEIGHT, theme=theme, rasterizer=rasterizer)
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
        for kind in ("cut", "static", "zoom"):
            save(f"transition-{kind}.png", _transition(kind, plain))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    args = parser.parse_args()
    with ChromiumRasterizer() as rasterizer:
        written = build_thumbnails(args.out, rasterizer=rasterizer, theme=load_theme(args.theme))
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
