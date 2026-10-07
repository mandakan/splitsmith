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


def _fake_ffmpeg(cmd, **_kwargs):  # type: ignore[no-untyped-def]
    """Stands in for ffmpeg's xfade at authoring time: writes the frames
    the command asks for (``-frames:v N`` into the ``%03d`` pattern) as
    blends of the two ``-i`` stills, so the unit tests never shell out."""
    import subprocess

    argv = [str(c) for c in cmd]
    inputs = [argv[i + 1] for i, token in enumerate(argv) if token == "-i"]
    count = int(argv[argv.index("-frames:v") + 1])
    pattern = argv[-1]
    graph = argv[argv.index("-filter_complex") + 1]
    kind = graph.split("xfade=transition=", 1)[1].split(":", 1)[0]
    from PIL import ImageChops

    with Image.open(inputs[0]) as a, Image.open(inputs[1]) as b:
        # Each kind rolls the right still by its own amount, so two kinds'
        # loops differ as the real transitions would.
        left, right = a.convert("RGB"), ImageChops.offset(b.convert("RGB"), sum(map(ord, kind)) % 97, 0)
        for index in range(count):
            Image.blend(left, right, index / max(1, count - 1)).save(pattern % (index + 1))
    return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_writes_every_thumbnail_at_the_gallery_size(tmp_path: Path) -> None:
    mod = _load()
    raster = _StubRasterizer()
    written = mod.build_thumbnails(
        tmp_path, rasterizer=raster, look=load_look("splitsmith"), ffmpeg="ffmpeg", runner=_fake_ffmpeg
    )
    assert sorted(p.name for p in written) == sorted(mod.THUMBNAILS)
    for path in written:
        with Image.open(path) as im:
            assert im.size == (mod.WIDTH, mod.HEIGHT), path.name
            assert im.mode in ("RGB", "RGBA"), path.name
    # Every text card went through the rasterizer at the tile size.
    assert raster.calls and all(c == (mod.WIDTH, mod.HEIGHT) for c in raster.calls)


def test_file_set_matches_the_registry() -> None:
    """The TS registry names the files; the script must write exactly those."""
    mod = _load()
    source = REGISTRY.read_text(encoding="utf-8")
    referenced = set(re.findall(r'"([a-z0-9-]+\.(?:png|webp))"', source))
    assert referenced == set(mod.THUMBNAILS)


def test_transition_tiles_differ_from_each_other(tmp_path: Path) -> None:
    mod = _load()
    mod.build_thumbnails(
        tmp_path,
        rasterizer=_StubRasterizer(),
        look=load_look("splitsmith"),
        ffmpeg="ffmpeg",
        runner=_fake_ffmpeg,
    )
    names = [n for n in mod.THUMBNAILS if n.startswith("transition-")]
    assert len(names) == 13
    assert len({(tmp_path / n).read_bytes() for n in names}) == len(names)


def test_the_xfade_tiles_loop_as_animated_webp(tmp_path: Path) -> None:
    """Slice 6 (#1246): an xfade tile is the real transition between the
    two stills, through the project ffmpeg at authoring time, saved as a
    looping WebP: a quarter second of each side held around a one second
    fade at 12 fps; the cut and the two FCP effects stay stills."""
    mod = _load()
    calls: list[list[str]] = []

    def recording(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append([str(c) for c in cmd])
        return _fake_ffmpeg(cmd, **kwargs)

    mod.build_thumbnails(
        tmp_path,
        rasterizer=_StubRasterizer(),
        look=load_look("splitsmith"),
        ffmpeg="/bin/ff",
        runner=recording,
    )
    webps = sorted(p.name for p in tmp_path.glob("transition-*.webp"))
    assert webps == sorted(f"transition-{kind}.webp" for kind in mod.XFADE_LOOP_KINDS)
    assert {p.name for p in tmp_path.glob("transition-*.png")} == {
        "transition-cut.png",
        "transition-static.png",
        "transition-zoom.png",
    }
    with Image.open(tmp_path / "transition-fade.webp") as loop:
        assert loop.format == "WEBP" and loop.n_frames == 18 and loop.size == (mod.WIDTH, mod.HEIGHT)
        assert loop.info.get("loop") == 0
    fade = next(c for c in calls if "xfade=transition=fade:" in " ".join(c))
    assert fade[0] == "/bin/ff" and "-loop" in fade and fade[fade.index("-frames:v") + 1] == "18"
    assert "fps=12" in " ".join(fade)


def test_look_previews_write_one_file_per_variant_and_the_sample_tile(tmp_path: Path) -> None:
    """Slice 6 (#1246): the shipped Look's ``preview/`` set, drawn through
    the Look's own templates; a Look without templates of its own (clean)
    gets the same names through the shipped default's."""
    mod = _load()
    for name in ("splitsmith", "clean"):
        written = mod.build_look_previews(
            tmp_path, look=load_look(name), rasterizer=_StubRasterizer(), ffmpeg="ffmpeg", runner=_fake_ffmpeg
        )
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
            "transition-wipe.webp",
        }
        assert all(p.parent == tmp_path / name / "preview" for p in written)
        with Image.open(tmp_path / name / "preview" / "slate-rise.png") as image:
            assert image.size == (mod.WIDTH, mod.HEIGHT)


def test_a_sting_preview_loops_over_the_fade(tmp_path: Path) -> None:
    """The Look's sting preview is its template sampled frame by frame
    over the fade loop, so the gallery tile plays the sting."""
    mod = _load()
    raster = _StubRasterizer(motion_seconds=1.0)
    mod.build_look_previews(
        tmp_path, look=load_look("splitsmith"), rasterizer=raster, ffmpeg="ffmpeg", runner=_fake_ffmpeg
    )
    with Image.open(tmp_path / "splitsmith" / "preview" / "transition-wipe.webp") as loop:
        assert loop.format == "WEBP" and loop.n_frames == 18
    assert raster.frames_rendered == 12, "one second of sting at the loop's 12 fps"
    assert raster.frame_requests[-1][4] == 12 and raster.frame_requests[-1][5] == 1.0
