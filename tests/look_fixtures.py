"""Shared Look builders for tests that need a user Look on disk."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import looks
from splitsmith.look.template import TemplateContext
from splitsmith.looks import Look, load_look
from splitsmith.overlay.raster import TemplateFrames

FRAMES_STING_HTML = """<!doctype html>
<html>
<head>
<meta name="splitsmith-frames" content="edges">
</head>
<body>
<script>
window.duration = () => splitsmith.data.transition.duration_seconds;
window.seek = (t) => Promise.resolve();
</script>
</body>
</html>
"""


def install_frames_look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Look:
    """A user Look named ``framesy`` whose ``push`` sting declares
    ``splitsmith-frames``, installed under ``SPLITSMITH_HOME=tmp_path``."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    root = tmp_path / "looks" / "framesy"
    root.mkdir(parents=True)
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "framesy"
    manifest["slots"] = {"transition": {"push": "push.html"}}
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "push.html").write_text(FRAMES_STING_HTML, encoding="utf-8")
    return load_look("framesy")


#: The transparent column :class:`PushRasterizer` leaves, as fractions of the width.
PUSH_GAP = (0.45, 0.55)


class PushRasterizer:
    """A frames sting drawn in Python (#1535): it reads the frames the
    renderer handed it from the mount and lays the incoming frame's left
    half beside the outgoing frame's right half, so a boundary pixel says
    which edge it came from. A column in the middle (:data:`PUSH_GAP`, as
    fractions of the width) is left transparent, the part a frames sting
    shows as black and an overlay sting as whatever is under it; the rest
    is opaque. The decision-time load
    (no frames) reports ``sounds`` as a template's ``window.sounds()``
    would. Counts its loads and the frames it drew."""

    def __init__(self, sounds: tuple[dict[str, Any], ...] = ({"play": "x.m4a", "at": 0.5},)) -> None:
        self.sounds = sounds
        self.loads = 0
        self.frames_drawn = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def render_template(self, template: Path, *, context: TemplateContext, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def engine_version(self) -> str:
        return "push"

    def render_template_frames(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        fps: float,
        max_seconds: float,
        frames_dir: Path | None = None,
    ) -> TemplateFrames:
        self.loads += 1
        count = max(1, math.ceil(max_seconds * fps - 1e-9))
        frames = context.data["transition"].get("frames")

        def picture(url: str) -> Image.Image:
            assert frames_dir is not None, "frames handed over without the mount"
            with Image.open(frames_dir / url.split("/frames/", 1)[1]) as image:
                return image.convert("RGBA").resize((width, height))

        def generate() -> Iterator[bytes]:
            for n in range(count):
                self.frames_drawn += 1
                out = Image.new("RGBA", (width, height), (0, 0, 0, 255))
                if frames is not None:
                    out.paste(picture(frames["to"][n]).crop((0, 0, width // 2, height)), (0, 0))
                    out.paste(
                        picture(frames["from"][n]).crop((width // 2, 0, width, height)), (width // 2, 0)
                    )
                out.paste((0, 0, 0, 0), (int(width * PUSH_GAP[0]), 0, int(width * PUSH_GAP[1]), height))
                yield out.tobytes()

        return TemplateFrames(
            duration=max_seconds, frame_count=count, width=width, height=height, frames=generate(),
            sounds=self.sounds,
        )  # fmt: skip
