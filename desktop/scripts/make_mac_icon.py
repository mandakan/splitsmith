"""Build desktop/build-resources/icon.icns from the splitsmith mark.

The mark is site/favicon.svg (a 36-unit square with its own rounded tile).
macOS draws app icons on a 1024 px canvas with the tile 824 px wide and a
100 px margin (the Big Sur grid), so the mark is scaled onto that, drawn
with resvg at 1024 px, resized to every iconset size and packed with
iconutil (macOS only).

    uv run python desktop/scripts/make_mac_icon.py
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MARK = ROOT / "site" / "favicon.svg"
OUT = ROOT / "desktop" / "build-resources" / "icon.icns"
#: The mark's tile spans 1.5..34.5 of its 36-unit viewBox.
TILE_START, TILE_SIZE = 1.5, 33.0
CANVAS, TILE_PX, MARGIN_PX = 1024, 824, 100


def main() -> None:
    svg = MARK.read_text(encoding="utf-8")
    inner = svg[svg.index(">") + 1 : svg.rindex("</svg>")]
    scale = TILE_PX / TILE_SIZE
    offset = MARGIN_PX - TILE_START * scale
    wrapped = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" '
        f'viewBox="0 0 {CANVAS} {CANVAS}"><g transform="translate({offset:.4f},{offset:.4f}) '
        f'scale({scale:.6f})">{inner}</g></svg>'
    )
    png = bytes(resvg_py.svg_to_bytes(svg_string=wrapped, width=CANVAS))
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Splitsmith.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for factor in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if factor == 2 else ''}.png"
                image.resize((size * factor, size * factor), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
