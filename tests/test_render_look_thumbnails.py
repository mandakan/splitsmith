"""``scripts/render_look_thumbnails.py``: the gallery's generic tiles.

The rasterizer is a stub that returns one transparent PNG, so the test
runs without Chromium; what it pins is the file set, the size and that
every builder path composes without the browser doing anything.
"""

from __future__ import annotations

import importlib.util
import io
import re
from pathlib import Path

from PIL import Image

from splitsmith.overlay_theme import load_theme

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_look_thumbnails.py"
REGISTRY = Path(__file__).resolve().parent.parent / "src/splitsmith/ui_static/src/lib/lookGallery.ts"


def _load():
    spec = importlib.util.spec_from_file_location("render_look_thumbnails", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubRasterizer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((width, height))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def test_writes_every_thumbnail_at_the_gallery_size(tmp_path: Path) -> None:
    mod = _load()
    raster = _StubRasterizer()
    written = mod.build_thumbnails(tmp_path, rasterizer=raster, theme=load_theme("splitsmith"))
    assert sorted(p.name for p in written) == sorted(mod.THUMBNAILS)
    for path in written:
        with Image.open(path) as im:
            assert im.size == (mod.WIDTH, mod.HEIGHT), path.name
            assert im.mode == "RGB"
    # Every text card went through the rasterizer at the tile size.
    assert raster.calls and all(c == (mod.WIDTH, mod.HEIGHT) for c in raster.calls)


def test_file_set_matches_the_registry() -> None:
    """The TS registry names the files; the script must write exactly those."""
    mod = _load()
    source = REGISTRY.read_text(encoding="utf-8")
    referenced = set(re.findall(r'"([a-z0-9-]+\.png)"', source))
    assert referenced == set(mod.THUMBNAILS)


def test_transition_tiles_differ_from_each_other(tmp_path: Path) -> None:
    mod = _load()
    mod.build_thumbnails(tmp_path, rasterizer=_StubRasterizer(), theme=load_theme("splitsmith"))
    names = ("transition-cut.png", "transition-static.png", "transition-zoom.png")
    assert len({(tmp_path / n).read_bytes() for n in names}) == 3
