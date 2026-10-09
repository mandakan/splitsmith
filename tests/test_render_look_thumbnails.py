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
        self.hud_frames_rendered = 0
        self.hud_requests: list[tuple] = []

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

    def render_template_timeline(self, template, *, context, width: int, height: int, plan):
        """A HUD: ``plan`` gets a fixed settle; one blank frame per time,
        counted apart from the stings' frames as it is pulled."""
        from splitsmith.overlay_raster import TemplateFrames

        self.calls.append((width, height))
        self.hud_requests.append((template, context.model_dump(), width, height))
        times = list(plan(0.5))
        blank = bytes(width * height * 4)

        def frames():
            for _ in times:
                self.hud_frames_rendered += 1
                yield blank

        return TemplateFrames(
            duration=0.5, frame_count=len(times), width=width, height=height, frames=frames()
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
    # Every text card went through the rasterizer at the tile size, but the
    # grid's match summary: two renders stacked (its title strip and the
    # cells below it) at twice the tile, scaled down to it.
    assert raster.calls
    parts = [c for c in raster.calls if c != (mod.WIDTH, mod.HEIGHT)]
    assert len(parts) == 2 and all(w == 2 * mod.WIDTH for w, _h in parts)
    assert sum(h for _w, h in parts) == 2 * mod.HEIGHT


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
    assert len(names) == 3
    assert len({(tmp_path / n).read_bytes() for n in names}) == len(names)


def test_every_transition_family_gets_a_looping_preview(tmp_path: Path) -> None:
    """Issue #1259: one looping WebP per family, its first direction through
    the project ffmpeg, under the ``_transitions`` owner the Looks route
    serves; the bundled tiles are the cut and the two FCP effects only."""
    from splitsmith import composition

    mod = _load()
    calls: list[list[str]] = []

    def recording(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append([str(c) for c in cmd])
        return _fake_ffmpeg(cmd, **kwargs)

    written = mod.build_transition_previews(tmp_path, ffmpeg="/bin/ff", runner=recording)
    out = tmp_path / "_transitions" / "preview"
    assert sorted(p.name for p in written) == sorted(f"{f.id}.webp" for f in composition.XFADE_FAMILIES)
    assert all(p.parent == out for p in written)
    with Image.open(out / "wind.webp") as loop:
        assert loop.format == "WEBP" and loop.n_frames == 18 and loop.size == (mod.WIDTH, mod.HEIGHT)
        assert loop.info.get("loop") == 0
    wind = next(c for c in calls if "xfade=transition=hlwind:" in " ".join(c))
    assert wind[0] == "/bin/ff" and wind[wind.index("-frames:v") + 1] == "18"
    assert [n for n in mod.THUMBNAILS if n.startswith("transition-")] == [
        "transition-cut.png",
        "transition-static.png",
        "transition-zoom.png",
    ]


def test_the_shipped_transition_previews_cover_every_family() -> None:
    from splitsmith import composition
    from splitsmith.looks import shipped_looks_dir

    out = shipped_looks_dir() / "_transitions" / "preview"
    assert sorted(p.name for p in out.glob("*.webp")) == sorted(
        f"{f.id}.webp" for f in composition.XFADE_FAMILIES
    )


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
            "closing-end-screen.png",
            "closing-rise.png",
            "transition-wipe.webp",
            "overlay-minimal.webp",
            "overlay-pips.webp",
            "overlay-plate.webp",
            "overlay-ticker.webp",
            "overlay-timeline.webp",
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


def test_a_hud_preview_plays_a_sample_stage_through_the_landing(tmp_path: Path) -> None:
    """Each overlay style's gallery tile is the template run over a short
    sample stage from the beep through its landing, at the loop's rate,
    over the painted backdrop."""
    mod = _load()
    raster = _StubRasterizer()
    mod.build_look_previews(
        tmp_path, look=load_look("splitsmith"), rasterizer=raster, ffmpeg="ffmpeg", runner=_fake_ffmpeg
    )
    hud = raster.hud_requests
    assert [Path(r[0]).name for r in hud] == [
        "hud-minimal.html",
        "hud-pips.html",
        "hud-plate.html",
        "hud-ticker.html",
        "hud-timeline.html",
    ]
    stage = hud[0][1]["data"]["stage"]
    assert stage["rounds"] == len(mod.HUD_SAMPLE) and stage["beep"] == mod.HUD_LOOP_BEEP
    end = stage["shots"][-1]["t"] + 0.5 + mod.HUD_LOOP_TAIL
    # The stub draws blank frames, which the WebP encoder merges; count
    # what was rendered instead: every style, beep through landing.
    assert raster.hud_frames_rendered == 5 * (int(end * mod.LOOP_FPS) + 1)
    with Image.open(tmp_path / "splitsmith" / "preview" / "overlay-plate.webp") as loop:
        assert loop.format == "WEBP" and loop.size == (mod.WIDTH, mod.HEIGHT)
