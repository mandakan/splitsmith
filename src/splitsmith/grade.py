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
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np

from .looks import Look
from .runtime import quote_filter_value
from .user_config import user_config_dir

#: The largest LUT a ``.cube`` may declare: 65 points is the common ceiling
#: of grading tools; past it a table is megabytes of rows for nothing.
MAX_SIZE = 64
MIN_SIZE = 2
#: The largest ``.cube`` read, in bytes (a 64-point table is about 7 MB).
MAX_BYTES = 10 * 1024 * 1024
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
    """A ``.cube`` 3D LUT (the Adobe / Resolve text format). Refuses a 1D LUT,
    a size outside :data:`MIN_SIZE`..:data:`MAX_SIZE`, a domain other than
    0..1, a row that is not three numbers and a row count that is not
    ``size**3``; output values are clamped to 0..1."""
    size: int | None = None
    rows: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0]
        if head == "TITLE":
            continue
        if head == "LUT_1D_SIZE":
            raise GradeError("a 1D LUT; a grade needs a 3D LUT (LUT_3D_SIZE)")
        if head == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError) as exc:
                raise GradeError(f"LUT_3D_SIZE is not a number: {line!r}") from exc
            if not MIN_SIZE <= size <= MAX_SIZE:
                raise GradeError(f"LUT size {size} is outside {MIN_SIZE}..{MAX_SIZE}")
            continue
        if head in ("DOMAIN_MIN", "DOMAIN_MAX"):
            want = 0.0 if head == "DOMAIN_MIN" else 1.0
            try:
                values = [float(v) for v in line.split()[1:4]]
            except ValueError as exc:
                raise GradeError(f"{head} is not three numbers: {line!r}") from exc
            if len(values) != 3 or any(abs(v - want) > 1e-6 for v in values):
                raise GradeError(f"only a 0..1 domain is supported, not {line!r}")
            continue
        parts = line.split()
        try:
            values = [float(v) for v in parts]
        except ValueError as exc:
            raise GradeError(f"a row that is not a number: {line!r}") from exc
        if len(values) != 3:
            raise GradeError(f"a row that is not three numbers: {line!r}")
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
    return parse_cube(path.read_text(encoding="utf-8", errors="replace"))


def write_cube(lut: Lut, *, title: str = "splitsmith") -> str:
    flat = lut.table.reshape(-1, 3)
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat)
    return f'TITLE "{title}"\nLUT_3D_SIZE {lut.size}\n{rows}\n'


def blend(lut: Lut, strength: float) -> Lut:
    """``lut`` at ``strength``: 0 is the identity (no grade), 1 the LUT."""
    s = min(1.0, max(0.0, strength))
    return Lut(size=lut.size, table=identity(lut.size).table * (1.0 - s) + lut.table * s)


def materialize(resolved: ResolvedGrade, work_dir: Path) -> Path:
    """The blended LUT the render reads, written once into ``work_dir`` as
    ``grade-<12hex>.cube``, named by what it holds."""
    text = write_cube(blend(read_cube(resolved.source), resolved.strength), title=resolved.label)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"grade-{digest}.cube"
    if not out.exists():
        partial = out.with_name(f".{out.name}.partial")
        partial.write_text(text, encoding="utf-8")
        partial.replace(out)
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
    return ResolvedGrade(
        source=source, strength=spec.strength if strength is None else strength, label=look.name
    )


def resolve_grade(choice: str, look: Look, *, strength: float | None = None) -> ResolvedGrade | None:
    """The grade an export asked for: ``look`` (the Look's own, else none),
    ``none``, ``preset:<id>`` or ``user:<file>``. ``strength`` overrides the
    Look's (and the default 1.0). Raises :class:`GradeError` for a choice
    naming nothing real; ``None`` grades nothing."""
    if choice == "none":
        return None
    if choice == "look":
        return _look_grade(look, strength)
    s = 1.0 if strength is None else strength
    if choice.startswith("preset:"):
        name = choice.removeprefix("preset:")
        return ResolvedGrade(source=preset_path(name), strength=s, label=name)
    if choice.startswith("user:"):
        name = choice.removeprefix("user:")
        path = user_luts_dir() / name
        if not USER_LUT_RE.match(name) or path.is_symlink() or not path.is_file():
            raise GradeError(f"no LUT of yours named {name!r}")
        return ResolvedGrade(source=path, strength=s, label=name.rsplit("-", 1)[0])
    raise GradeError(f"unknown grade {choice!r}: look, none, preset:<id> or user:<file>")
