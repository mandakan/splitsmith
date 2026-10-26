"""Colour grades: 3D LUTs applied to a rendered video's footage.

A grade is a ``.cube`` 3D LUT and a strength. It comes from a Look
(``look.json``'s ``grade``: a LUT in the Look's own folder or a shipped
preset), from the shipped presets in ``data/luts/`` (``scripts/build_luts.py``
generates them; they are ours), or from a file the user brought
(``<user_config_dir>/luts/``, content-named). The renderers grade footage
only, never a card, the HUD or a logo.

Strength is baked into the table, not a blend in the filter graph: the render
reads a blended copy written into its work dir under a name made from its
content (:func:`materialize`), so the ffmpeg argv, and with it the segment
cache key, moves whenever the LUT or the strength does.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np
from PIL import Image

from . import fs_atomic
from .looks import Look
from .runtime import quote_filter_value
from .user_config import user_config_dir

#: The largest LUT a ``.cube`` may declare: 65 points is the common ceiling
#: of grading tools (ffmpeg's ``lut3d`` reads it); past it a table is
#: megabytes of rows for nothing.
MAX_SIZE = 65
MIN_SIZE = 2
#: The largest ``.cube`` read, in bytes: a 65-point table written with ten
#: decimals, as Resolve writes it, is about 11 MB.
MAX_BYTES = 16 * 1024 * 1024
#: Shipped preset ids and content-named user LUT files.
_PRESET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
USER_LUT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}-[0-9a-f]{12}\.cube$")


class GradeError(ValueError):
    """A LUT that cannot be read, or a grade choice that names nothing."""


@dataclass(frozen=True)
class Lut:
    """A 3D LUT: ``table[b][g][r]`` is the RGB (0..1) the input maps to, the
    ``.cube`` order (red varies fastest)."""

    size: int
    table: np.ndarray


@dataclass(frozen=True)
class ResolvedGrade:
    """The grade an export draws: a ``.cube`` on this disk and a strength."""

    source: Path
    strength: float
    label: str


def identity(size: int) -> Lut:
    axis = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    return Lut(size=size, table=np.stack([r, g, b], axis=-1))


def parse_cube(text: str) -> Lut:
    """A ``.cube`` 3D LUT (the Adobe / Resolve text format), as grading tools
    write it: a BOM, comments (whole lines or after a row), ``TITLE`` and
    0..1 input ranges (``DOMAIN_MIN`` / ``DOMAIN_MAX``,
    ``LUT_3D_INPUT_RANGE``, ``LUT_1D_INPUT_RANGE``) are read. Refuses a 1D
    LUT, a video-range one, a size outside :data:`MIN_SIZE`..:data:`MAX_SIZE`,
    another input range, a row that is not three finite numbers and a row
    count that is not ``size**3``; output values are clamped to 0..1."""
    size: int | None = None
    rows: list[list[float]] = []
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head = line.split()[0]
        if head == "TITLE":
            continue
        if head == "LUT_1D_SIZE":
            raise GradeError("a 1D LUT; a grade needs a 3D LUT (LUT_3D_SIZE)")
        if head in ("LUT_IN_VIDEO_RANGE", "LUT_OUT_VIDEO_RANGE"):
            raise GradeError("a video range LUT; export it as full range (data levels)")
        if head == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError) as exc:
                raise GradeError(f"LUT_3D_SIZE is not a number: {line!r}") from exc
            if not MIN_SIZE <= size <= MAX_SIZE:
                raise GradeError(f"LUT size {size} is outside {MIN_SIZE}..{MAX_SIZE}")
            continue
        if head in ("DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            try:
                values = [float(v) for v in line.split()[1:]]
            except ValueError as exc:
                raise GradeError(f"{head} is not numbers: {line!r}") from exc
            if head == "DOMAIN_MIN":
                want = [0.0] * 3
            elif head == "DOMAIN_MAX":
                want = [1.0] * 3
            else:
                want = [0.0, 1.0]
            if len(values) != len(want) or any(abs(v - w) > 1e-6 for v, w in zip(values, want, strict=True)):
                raise GradeError(f"only a 0..1 input range is supported, not {line!r}")
            continue
        try:
            values = [float(v) for v in line.split()]
        except ValueError as exc:
            raise GradeError(f"a row that is not a number: {line!r}") from exc
        if len(values) != 3:
            raise GradeError(f"a row that is not three numbers: {line!r}")
        if not all(np.isfinite(values)):
            raise GradeError(f"a row that is not finite numbers: {line!r}")
        rows.append(values)
    if size is None:
        raise GradeError("no LUT_3D_SIZE: not a 3D .cube file")
    if len(rows) != size**3:
        raise GradeError(f"LUT_3D_SIZE {size} wants {size**3} rows, the file has {len(rows)}")
    table = np.clip(np.asarray(rows, dtype=np.float64).reshape(size, size, size, 3), 0.0, 1.0)
    return Lut(size=size, table=table)


def read_cube(path: Path) -> Lut:
    if path.is_symlink() or not path.is_file():
        raise GradeError(f"{path.name} is not a file")
    if path.stat().st_size > MAX_BYTES:
        raise GradeError(f"{path.name} is over {MAX_BYTES // (1024 * 1024)} MB")
    return parse_cube(path.read_text(encoding="utf-8-sig", errors="replace"))


def write_cube(lut: Lut, *, title: str = "splitsmith") -> str:
    flat = lut.table.reshape(-1, 3)
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat)
    return f'TITLE "{title}"\nLUT_3D_SIZE {lut.size}\n{rows}\n'


def blend(lut: Lut, strength: float) -> Lut:
    """``lut`` at ``strength``: 0 is the identity (no grade), 1 the LUT."""
    s = min(1.0, max(0.0, strength))
    return Lut(size=lut.size, table=identity(lut.size).table * (1.0 - s) + lut.table * s)


def _blended_text(resolved: ResolvedGrade) -> str:
    return write_cube(blend(read_cube(resolved.source), resolved.strength), title=resolved.label)


def grade_digest(resolved: ResolvedGrade) -> str:
    """What the grade draws, as 12 hex digits: the render's file name
    (:func:`materialize`) and the preview's cache key."""
    return hashlib.sha256(_blended_text(resolved).encode()).hexdigest()[:12]


def materialize(resolved: ResolvedGrade, work_dir: Path) -> Path:
    """The blended LUT the render reads, written once into ``work_dir`` as
    ``grade-<12hex>.cube``, named by what it holds."""
    text = _blended_text(resolved)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"grade-{digest}.cube"
    if not out.exists():
        partial = out.with_name(f".{out.name}.partial")
        partial.write_text(text, encoding="utf-8")
        fs_atomic.replace(partial, out)
    return out


def lut_filter(path: Path) -> str:
    """The ``lut3d`` filter that grades with ``path``."""
    return f"lut3d=file={quote_filter_value(str(path))}:interp=tetrahedral"


def presets_dir() -> Path:
    return Path(str(resources.files("splitsmith.data").joinpath("luts")))


def preset_names() -> list[str]:
    return sorted(p.stem for p in presets_dir().glob("*.cube"))


def preset_path(name: str) -> Path:
    if not _PRESET_RE.match(name) or not (presets_dir() / f"{name}.cube").is_file():
        raise GradeError(f"no grade preset named {name!r}; there are {', '.join(preset_names())}")
    return presets_dir() / f"{name}.cube"


def user_luts_dir() -> Path:
    """``<user_config_dir>/luts``: LUTs the user brought (desktop only)."""
    return user_config_dir() / "luts"


def _look_grade(look: Look, strength: float | None) -> ResolvedGrade | None:
    spec = look.manifest.grade
    if spec is None:
        return None
    if spec.lut:
        root = look.root.resolve()
        path = (look.root / spec.lut).resolve()
        if root not in path.parents or (look.root / spec.lut).is_symlink() or not path.is_file():
            raise GradeError(f"Look {look.name}: grade {spec.lut!r} is not a file in the Look's folder")
        source = path
    elif spec.preset:
        source = preset_path(spec.preset)
    else:
        return None
    return _checked(source, spec.strength if strength is None else strength, look.name)


def _checked(source: Path, strength: float, label: str) -> ResolvedGrade | None:
    """The grade, once its LUT has been read (so a file that cannot be is a
    :class:`GradeError` here, never in the render); ``None`` at strength 0,
    which is no grade at all."""
    read_cube(source)
    if strength <= 0.0:
        return None
    return ResolvedGrade(source=source, strength=min(1.0, strength), label=label)


def resolve_grade(choice: str, look: Look | None, *, strength: float | None = None) -> ResolvedGrade | None:
    """The grade an export asked for: ``look`` (the Look's own, else none),
    ``none``, ``preset:<id>`` or ``user:<file>``. ``strength`` overrides the
    Look's (and the default 1.0). Raises :class:`GradeError` for a choice
    naming nothing real; ``None`` grades nothing."""
    if choice == "none":
        return None
    if choice == "look":
        return _look_grade(look, strength) if look is not None else None
    s = 1.0 if strength is None else strength
    if choice.startswith("preset:"):
        name = choice.removeprefix("preset:")
        return _checked(preset_path(name), s, name)
    if choice.startswith("user:"):
        name = choice.removeprefix("user:")
        path = user_luts_dir() / name
        if not USER_LUT_RE.match(name) or path.is_symlink() or not path.is_file():
            raise GradeError(f"no LUT of yours named {name!r}")
        return _checked(path, s, name.rsplit("-", 1)[0])
    raise GradeError(f"unknown grade {choice!r}: look, none, preset:<id> or user:<file>")


def _slug(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40].strip("-")
    return slug or "lut"


def add_user_lut(source: Path) -> str:
    """Check ``source`` is a 3D ``.cube`` this module reads and keep a copy in
    :func:`user_luts_dir` as ``<slug>-<12hex>.cube``, named by its bytes (the
    same file twice is one copy). Returns the name a ``user:`` grade takes.
    Raises :class:`GradeError` for a file it would not read."""
    # The user named this file themselves: a link is followed. The copy kept
    # is the bytes, never a link.
    real = source.resolve()
    if not real.is_file():
        raise GradeError(f"{source.name} is not a file")
    if real.stat().st_size > MAX_BYTES:
        raise GradeError(f"{source.name} is over {MAX_BYTES // (1024 * 1024)} MB")
    return add_user_lut_bytes(real.read_bytes(), source.stem)


def add_user_lut_bytes(data: bytes, stem: str) -> str:
    """:func:`add_user_lut` for bytes already read (the app's upload);
    ``stem`` names it, slugged, never trusted as a path."""
    if len(data) > MAX_BYTES:
        raise GradeError(f"the LUT is over {MAX_BYTES // (1024 * 1024)} MB")
    parse_cube(data.decode("utf-8-sig", errors="replace"))
    name = f"{_slug(stem)}-{hashlib.sha256(data).hexdigest()[:12]}.cube"
    folder = user_luts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / name
    if not out.exists():
        partial = folder / f".{name}.partial"
        partial.write_bytes(data)
        fs_atomic.replace(partial, out)
    return name


def list_user_luts() -> list[str]:
    folder = user_luts_dir()
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if USER_LUT_RE.match(p.name) and not p.is_symlink())


def apply_lut(image: Image.Image, lut: Lut) -> Image.Image:
    """``image`` graded through ``lut`` (trilinear), for stills: the gallery's
    thumbnails and the export preview's frame. The render uses ffmpeg's
    ``lut3d`` (tetrahedral); the two differ by a level or two."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
    n = lut.size
    pos = rgb * (n - 1)
    lo = np.clip(np.floor(pos).astype(int), 0, n - 2)
    frac = pos - lo
    r0, g0, b0 = lo[..., 0], lo[..., 1], lo[..., 2]
    fr, fg, fb = frac[..., 0:1], frac[..., 1:2], frac[..., 2:3]
    t = lut.table  # [b][g][r]

    def at(db: int, dg: int, dr: int) -> np.ndarray:
        return t[b0 + db, g0 + dg, r0 + dr]

    c00 = at(0, 0, 0) * (1 - fr) + at(0, 0, 1) * fr
    c01 = at(0, 1, 0) * (1 - fr) + at(0, 1, 1) * fr
    c10 = at(1, 0, 0) * (1 - fr) + at(1, 0, 1) * fr
    c11 = at(1, 1, 0) * (1 - fr) + at(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    out = c0 * (1 - fb) + c1 * fb
    return Image.fromarray(np.clip(np.round(out * 255.0), 0, 255).astype(np.uint8), "RGB")


def swatch_scene(width: int = 320, height: int = 180) -> Image.Image:
    """A drawn outdoor scene a grade shows on: blue sky, a dark tree line,
    grass, an orange container with a red stripe, a hand's skin tone, and a
    grey ramp from black to white along the bottom. Ours, nothing filmed."""
    w, h = width, height
    img = np.zeros((h, w, 3), dtype=np.float64)
    y = np.linspace(0.0, 1.0, h)[:, None]
    horizon, trees, ramp = int(h * 0.38), int(h * 0.48), int(h * 0.88)
    sky = np.array([60, 120, 200]) * (1 - y[:horizon]) + np.array([175, 208, 236]) * y[:horizon] / max(
        y[horizon], 1e-6
    )
    img[:horizon] = np.clip(sky, 0, 255)[:, None, :]
    xs = np.linspace(0.0, 1.0, w)
    wobble = (np.sin(xs * 37.0) * 0.5 + 0.5)[None, :, None]
    img[horizon:trees] = np.array([34, 78, 38]) * (0.8 + 0.4 * wobble)
    grass_y = np.linspace(0.0, 1.0, ramp - trees)[:, None, None]
    img[trees:ramp] = np.array([88, 140, 58]) * (1 - grass_y) + np.array([150, 150, 82]) * grass_y
    # The container: orange with a red stripe.
    cx0, cx1, cy0, cy1 = int(w * 0.52), int(w * 0.92), int(h * 0.30), int(h * 0.74)
    img[cy0:cy1, cx0:cx1] = [208, 108, 48]
    img[int(h * 0.60) : int(h * 0.66), cx0:cx1] = [196, 28, 30]
    # A hand.
    img[int(h * 0.62) : ramp, int(w * 0.18) : int(w * 0.32)] = [226, 176, 140]
    # The ramp, with pure black and white at its ends.
    img[ramp:] = np.linspace(0.0, 255.0, w)[None, :, None]
    img[ramp:, : max(2, w // 20)] = 0.0
    img[ramp:, -max(2, w // 20) :] = 255.0
    return Image.fromarray(np.clip(np.round(img), 0, 255).astype(np.uint8), "RGB")


def preview_png(
    resolved: ResolvedGrade | None, *, cache_dir: Path, width: int = 320, height: int = 180
) -> bytes:
    """The gallery's thumbnail for a grade (``None`` is the scene as drawn),
    cached under ``cache_dir`` by what it draws."""
    key = grade_digest(resolved) if resolved is not None else "none"
    path = cache_dir / f"{key}-{width}x{height}.png"
    if path.is_file():
        return path.read_bytes()
    scene = swatch_scene(width, height)
    image = apply_lut(scene, blend(read_cube(resolved.source), resolved.strength)) if resolved else scene
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_bytes(data)
    fs_atomic.replace(partial, path)
    return data
