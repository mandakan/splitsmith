"""Generate the shipped colour grade presets (``src/splitsmith/data/luts``).

Every preset is computed here from a few plain operations (saturation, an
S-curve, lift and gain, split toning), so the tables are ours to ship.
Shipped Looks that carry their own LUT are built here too
(``LOOK_LUTS``, written into the Look's folder). Run after changing a
recipe and commit the ``.cube`` files:

    uv run python scripts/build_luts.py

The helpers are importable (``grade_table``) for a Look that wants its own
LUT built the same way.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import numpy as np

from splitsmith.grade import Lut, identity, write_cube

SIZE = 17
DATA = Path(__file__).resolve().parents[1] / "src" / "splitsmith" / "data"
OUT = DATA / "luts"

Rgb = np.ndarray  # (..., 3) in 0..1


def luma(rgb: Rgb) -> np.ndarray:
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def saturate(rgb: Rgb, amount: float) -> Rgb:
    y = luma(rgb)[..., None]
    return y + (rgb - y) * amount


def s_curve(rgb: Rgb, amount: float) -> Rgb:
    """Contrast around mid grey: 0 is none, 1 a full smoothstep."""
    smooth = rgb * rgb * (3.0 - 2.0 * rgb)
    return rgb + (smooth - rgb) * amount


def levels(rgb: Rgb, black: float = 0.0, white: float = 1.0, gamma: float = 1.0) -> Rgb:
    """Map input ``black..white`` to 0..1, then a gamma (>1 darkens mids)."""
    out = np.clip((rgb - black) / (white - black), 0.0, 1.0)
    return out**gamma


def lift_gain(rgb: Rgb, lift: float = 0.0, gain: float = 1.0) -> Rgb:
    return lift + rgb * (gain - lift)


def split_tone(rgb: Rgb, shadows: tuple[float, float, float], highlights: tuple[float, float, float]) -> Rgb:
    """Add ``shadows`` weighted toward black and ``highlights`` toward white.
    The shadow tint fades out in the last stretch to black, so true black
    stays black (a tinted black reads as a coloured lens rim)."""
    y = luma(rgb)[..., None]
    shadow_weight = (1.0 - y) ** 2 * np.clip(y / 0.15, 0.0, 1.0)
    return rgb + np.asarray(shadows) * shadow_weight + np.asarray(highlights) * y**2


def channel_gain(rgb: Rgb, r: float = 1.0, g: float = 1.0, b: float = 1.0) -> Rgb:
    return rgb * np.array([r, g, b])


def luma_contrast(rgb: Rgb, amount: float) -> Rgb:
    """The S-curve on brightness alone: every channel moves by the same
    amount, so colour differences, a light's colour cast among them, are
    not stretched the way a per-channel curve stretches them."""
    y = luma(rgb)[..., None]
    return rgb + (s_curve(y, amount) - y)


def _smoothstep(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def vibrance(rgb: Rgb, amount: float) -> Rgb:
    """Lift muted colour more than loud colour (skin and sky gain, an orange
    barricade does not shout), but leave near-grey alone, where a colour
    cast lives (a tungsten-lit wall is a slightly warm grey), and the deep
    shadows, where indoor noise lives."""
    y = luma(rgb)[..., None]
    sat = rgb.max(axis=-1, keepdims=True) - rgb.min(axis=-1, keepdims=True)
    weight = _smoothstep(sat, 0.06, 0.18) * (1.0 - sat) ** 2 * _smoothstep(y, 0.06, 0.30)
    return y + (rgb - y) * (1.0 + amount * weight)


def grade_table(recipe: Callable[[Rgb], Rgb], size: int = SIZE) -> Lut:
    return Lut(size=size, table=np.clip(recipe(identity(size).table), 0.0, 1.0))


PRESETS: dict[str, Callable[[Rgb], Rgb]] = {
    "vivid": lambda c: s_curve(saturate(c, 1.35), 0.25),
    "warm-film": lambda c: saturate(
        split_tone(
            lift_gain(s_curve(c, 0.2), lift=0.04, gain=0.97), (0.03, 0.01, -0.02), (0.11, 0.03, -0.12)
        ),
        0.9,
    ),
    "teal-orange": lambda c: saturate(
        split_tone(s_curve(c, 0.25), (-0.12, 0.03, 0.13), (0.13, 0.02, -0.13)),
        1.1,
    ),
    "cool-steel": lambda c: s_curve(saturate(channel_gain(c, r=0.9, g=1.0, b=1.1), 0.75), 0.25),
    "bleach-bypass": lambda c: s_curve(saturate(c, 0.45), 0.45),
    "faded": lambda c: saturate(lift_gain(c, lift=0.08, gain=0.92), 0.8),
    "noir": lambda c: np.repeat(s_curve(luma(c)[..., None], 0.5), 3, axis=-1),
}


#: Shipped Looks' own LUTs, by path under ``data/looks``. Broadcast's is
#: a sports-broadcast grade that holds up under indoor light: contrast on
#: brightness only, colour lifted where it is muted, cool shadows and no
#: warm tint (indoors, a warm tint and a per-channel curve both push a
#: tungsten cast further).
LOOK_LUTS: dict[str, Callable[[Rgb], Rgb]] = {
    "broadcast/luts/broadcast.cube": lambda c: split_tone(
        vibrance(luma_contrast(c, 0.32), 0.5), (-0.008, 0.0, 0.018), (0.0, 0.0, 0.0)
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, recipe in PRESETS.items():
        (args.out / f"{name}.cube").write_text(write_cube(grade_table(recipe), title=name), encoding="utf-8")
        print("wrote", name)
    if args.out == OUT:
        for rel, recipe in LOOK_LUTS.items():
            path = DATA / "looks" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(write_cube(grade_table(recipe), title=path.stem), encoding="utf-8")
            print("wrote", rel)


if __name__ == "__main__":
    main()
