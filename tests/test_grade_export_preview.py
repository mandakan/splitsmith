"""The export preview shows the export's colour grade on the footage it draws
the card over, and keys on it only when one is set."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from splitsmith import export_preview as ep
from splitsmith import grade
from splitsmith.grade import Lut, ResolvedGrade
from splitsmith.looks import load_look

from .test_export_preview import _project, _StubRasterizer


def _inverting(tmp_path: Path) -> ResolvedGrade:
    source = tmp_path / "invert.cube"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)), encoding="utf-8")
    return ResolvedGrade(source=source, strength=1.0, label="invert")


def _frame(tmp_path: Path, name: str, resolved: ResolvedGrade | None) -> np.ndarray:
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    project, root = _project(tmp_path / name)
    png = ep.render_preview(
        ep.PreviewSpec(card="frame", stage_number=3, width=480, backdrop="demo"),
        project=project,
        root=root,
        audit_doc=None,
        look=load_look("splitsmith"),
        rasterizer=_StubRasterizer(),
        ffmpeg_binary=None,
        work_dir=tmp_path / name / "work",
        grade=resolved,
    )
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=int)


def test_the_preview_frame_is_graded(tmp_path: Path) -> None:
    plain = _frame(tmp_path, "plain", None)
    graded = _frame(tmp_path, "graded", _inverting(tmp_path))
    assert np.abs(graded - (255 - plain)).mean() < 2, "the inverting grade inverts the drawn scene"


def test_the_key_moves_with_a_grade_and_only_then(tmp_path: Path) -> None:
    spec = ep.PreviewSpec(card="title", stage_number=1)
    key = ep.preview_key(spec, slug="me", project_updated_at="t", audit="a")
    same = ep.preview_key(ep.PreviewSpec(card="title", stage_number=1, grade_digest=None), slug="me",
                          project_updated_at="t", audit="a")  # fmt: skip
    graded = ep.PreviewSpec(
        card="title", stage_number=1, grade_digest=grade.grade_digest(_inverting(tmp_path))
    )
    assert key == same
    assert ep.preview_key(graded, slug="me", project_updated_at="t", audit="a") != key
