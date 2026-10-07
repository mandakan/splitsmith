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

from splitsmith.looks import load_look

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_look_thumbnails.py"
REGISTRY = Path(__file__).resolve().parent.parent / "src/splitsmith/ui_static/src/lib/lookGallery.ts"


def _load():
    spec = importlib.util.spec_from_file_location("render_look_thumbnails", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubRasterizer:
    def __init__(self, *, motion_seconds: float = 0.0) -> None:
        self.calls: list[tuple[int, int]] = []
        self.motion_seconds = motion_seconds
        self.frame_requests: list[tuple] = []
        self.frames_rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((width, height))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def render_template(self, template, *, context, width: int, height: int) -> bytes:
        if "transition" in context.data:
            # A sting tile: the template paints its band, the stub a square,
            # so the tile differs from the fade it rides as the real one does.
            buf = io.BytesIO()
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            image.paste((255, 45, 45, 255), (width // 3, 0, 2 * width // 3, height))
            image.save(buf, format="PNG")
            return buf.getvalue()
        return self.png("", width=width, height=height)

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(
        self, template, *, context, width: int, height: int, fps: float, max_seconds: float
    ):
        """A still unless ``motion_seconds`` is set; frames are blank and
        counted in ``frames_rendered`` as they are pulled."""
        import math

        from splitsmith.overlay_raster import TemplateFrames

        self.calls.append((width, height))
        self.frame_requests.append((template, context.model_dump(), width, height, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))
        blank = bytes(width * height * 4)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield blank

        return TemplateFrames(
            duration=duration, frame_count=count, width=width, height=height, frames=frames()
        )


def test_writes_every_thumbnail_at_the_gallery_size(tmp_path: Path) -> None:
    mod = _load()
    raster = _StubRasterizer()
    written = mod.build_thumbnails(tmp_path, rasterizer=raster, look=load_look("splitsmith"))
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
    mod.build_thumbnails(tmp_path, rasterizer=_StubRasterizer(), look=load_look("splitsmith"))
    names = [n for n in mod.THUMBNAILS if n.startswith("transition-")]
    assert len(names) == 13
    assert len({(tmp_path / n).read_bytes() for n in names}) == len(names)


def test_look_previews_write_one_file_per_variant_and_the_sample_tile(tmp_path: Path) -> None:
    """Slice 6 (#1246): the shipped Look's ``preview/`` set, drawn through
    the Look's own templates; a Look without templates of its own (clean)
    gets the same names through the shipped default's."""
    mod = _load()
    for name in ("splitsmith", "clean"):
        written = mod.build_look_previews(tmp_path, look=load_look(name), rasterizer=_StubRasterizer())
        assert {p.name for p in written} == {
            "look.png",
            "title_page-default.png",
            "title_page-rise.png",
            "slate-default.png",
            "slate-rise.png",
            "lower_third-default.png",
            "lower_third-rise.png",
            "closing-default.png",
            "closing-rise.png",
            "transition-wipe.png",
        }
        assert all(p.parent == tmp_path / name / "preview" for p in written)
        with Image.open(tmp_path / name / "preview" / "slate-rise.png") as image:
            assert image.size == (mod.WIDTH, mod.HEIGHT)
