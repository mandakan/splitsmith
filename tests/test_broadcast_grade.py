"""The Broadcast Look's own colour grade: the reference for a Look that ships
its own LUT. A sports-broadcast grade that must hold up under indoor light,
so it never stretches a light's colour cast, crushes the blacks or clips
the whites."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

from splitsmith import grade, looks

_LUT = looks.shipped_looks_dir() / "broadcast" / "luts" / "broadcast.cube"


def _through(rgb: tuple[int, int, int]) -> np.ndarray:
    """One colour through the shipped LUT, as the preview applies it, in 0..1."""
    image = Image.new("RGB", (1, 1), rgb)
    out = grade.apply_lut(image, grade.read_cube(_LUT)).getpixel((0, 0))
    return np.asarray(out, dtype=float) / 255.0


def _sat(rgb: np.ndarray) -> float:
    return float(rgb.max() - rgb.min())


def test_broadcast_grades_with_its_own_lut_at_full_strength() -> None:
    resolved = grade.resolve_grade("look", looks.load_look("broadcast"))
    assert resolved is not None
    assert resolved.source == _LUT.resolve() and resolved.strength == 1.0


def test_the_shipped_lut_is_what_its_recipe_builds() -> None:
    """``scripts/build_luts.py`` is the source; the file is its output. A
    hand edit, or a recipe changed without rebuilding, fails here. Compared
    within rounding: the last printed digit differs between machines."""
    spec = importlib.util.spec_from_file_location(
        "build_luts", Path(__file__).resolve().parents[1] / "scripts" / "build_luts.py"
    )
    assert spec is not None and spec.loader is not None
    build_luts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_luts)
    built = build_luts.grade_table(build_luts.LOOK_LUTS["broadcast/luts/broadcast.cube"])
    shipped = grade.read_cube(_LUT)
    assert shipped.size == built.size
    assert np.abs(shipped.table - built.table).max() < 1e-5


def test_black_and_white_stay_put() -> None:
    assert np.abs(_through((0, 0, 0))).max() < 2 / 255
    assert np.abs(_through((255, 255, 255)) - 1.0).max() < 2 / 255


def test_a_lights_colour_cast_is_not_pushed_further() -> None:
    """Indoors a wall lit by tungsten or LED is a slightly warm grey. A
    per-channel curve or a colour lift on near-grey makes that cast worse;
    this grade leaves near-grey alone."""
    for cast in [(134, 128, 121), (70, 64, 58), (200, 194, 186)]:
        before = np.asarray(cast, dtype=float) / 255.0
        assert _sat(_through(cast)) - _sat(before) <= 3 / 255, cast


def test_muted_colour_is_lifted_and_loud_colour_is_not() -> None:
    """Skin, sky and grass gain colour; a fully saturated orange barricade
    does not get louder."""
    for muted in [(191, 143, 112), (128, 175, 239), (96, 143, 48)]:
        before = np.asarray(muted, dtype=float) / 255.0
        assert _sat(_through(muted)) > _sat(before) * 1.08, muted
    orange = (255, 128, 0)
    assert _sat(_through(orange)) <= _sat(np.asarray(orange, dtype=float) / 255.0) + 1 / 255
