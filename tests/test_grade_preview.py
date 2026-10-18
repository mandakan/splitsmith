"""Grades on still images: the gallery's thumbnails and the export preview's
frame, drawn in Python (a request never runs ffmpeg) and matching the
render's ``lut3d`` closely."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from splitsmith import grade
from splitsmith.grade import Lut, ResolvedGrade


def _inverting(tmp_path: Path) -> ResolvedGrade:
    source = tmp_path / "invert.cube"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)))
    return ResolvedGrade(source=source, strength=1.0, label="invert")


def test_the_identity_changes_nothing_and_an_inverting_lut_inverts() -> None:
    image = grade.swatch_scene(160, 90)
    same = np.asarray(grade.apply_lut(image, grade.identity(17)), dtype=int)
    assert np.abs(same - np.asarray(image, dtype=int)).max() <= 1
    inverted = np.asarray(grade.apply_lut(image, Lut(size=2, table=1.0 - grade.identity(2).table)), dtype=int)
    assert np.abs(inverted - (255 - np.asarray(image, dtype=int))).max() <= 1


def test_the_swatch_scene_has_what_a_grade_shows_on() -> None:
    """Sky, grass, a warm orange, a red, skin, a grey ramp, black and white."""
    pixels = np.asarray(grade.swatch_scene(320, 180)).reshape(-1, 3)
    colours = {tuple(p) for p in pixels}
    assert (0, 0, 0) in colours and (255, 255, 255) in colours
    assert any(g > r + 40 and g > b + 20 for r, g, b in colours), "green"
    assert any(b > r + 40 for r, g, b in colours), "blue sky"
    assert any(r > g + 60 and r > b + 80 for r, g, b in colours), "red / orange"


def test_the_digest_is_the_render_files_name(tmp_path: Path) -> None:
    resolved = _inverting(tmp_path)
    assert grade.materialize(resolved, tmp_path / "w").name == f"grade-{grade.grade_digest(resolved)}.cube"


def test_a_preview_thumbnail_is_cached_by_its_digest(tmp_path: Path) -> None:
    resolved = _inverting(tmp_path)
    png = grade.preview_png(resolved, cache_dir=tmp_path / "cache")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (
        list((tmp_path / "cache").glob("*.png"))
        and grade.preview_png(resolved, cache_dir=tmp_path / "cache") == png
    )
    plain = grade.preview_png(None, cache_dir=tmp_path / "cache")
    assert plain != png


@pytest.mark.integration
def test_the_python_grade_matches_ffmpegs(tmp_path: Path) -> None:
    """The preview must show what the render draws: apply a real preset both
    ways and compare (tetrahedral vs trilinear differ by a level or two)."""
    resolved = ResolvedGrade(source=grade.preset_path("teal-orange"), strength=1.0, label="t")
    scene = tmp_path / "scene.png"
    grade.swatch_scene(160, 90).save(scene)
    lut = grade.materialize(resolved, tmp_path / "w")
    out = tmp_path / "ff.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(scene),
         "-vf", f"format=rgb24,{grade.lut_filter(lut)}", "-frames:v", "1", str(out)],
        check=True,
    )  # fmt: skip
    ours = np.asarray(grade.apply_lut(Image.open(scene), grade.read_cube(lut)), dtype=int)
    theirs = np.asarray(Image.open(out).convert("RGB"), dtype=int)
    assert np.abs(ours - theirs).mean() < 1.5 and np.percentile(np.abs(ours - theirs), 99) <= 6
