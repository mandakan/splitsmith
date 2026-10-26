"""What real grading tools write, what would draw garbage, and when a grade
is checked: findings of the colour-grade review."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from splitsmith import grade, looks
from splitsmith.grade import GradeError

from .test_grade import _cube, _identity_rows, _look_with


@pytest.fixture
def user_looks(tmp_path: Path):
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    yield tmp_path / "looks"
    looks.reset_user_looks_provider(token)


@pytest.mark.parametrize(
    "header",
    ["﻿", "LUT_3D_INPUT_RANGE 0.0 1.0\n", "LUT_1D_INPUT_RANGE 0 1\n", "# made by a tool\n"],
)
def test_what_grading_tools_write_is_read(header: str) -> None:
    """A BOM, the input-range line Resolve writes and trailing comments were
    refused."""
    rows = _identity_rows(2)
    rows[0] = rows[0] + "  # first row"
    lut = grade.parse_cube(header + _cube(2, rows))
    assert np.allclose(lut.table, grade.identity(2).table)


def test_a_65_point_lut_is_read() -> None:
    assert grade.parse_cube(_cube(65, _identity_rows(65))).size == 65


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_cube(2, ["nan nan nan"] + _identity_rows(2)[1:]), "finite"),
        (_cube(2, ["inf 0 0"] + _identity_rows(2)[1:]), "finite"),
        ("LUT_IN_VIDEO_RANGE\n" + _cube(2, _identity_rows(2)), "video range"),
        ("LUT_3D_INPUT_RANGE 0 4\n" + _cube(2, _identity_rows(2)), "0..1"),
    ],
)
def test_a_lut_that_would_draw_garbage_is_refused(text: str, match: str) -> None:
    """A NaN row reached the render and wrecked pixels."""
    with pytest.raises(GradeError, match=match):
        grade.parse_cube(text)


def test_a_looks_unparseable_lut_is_refused_when_resolved(user_looks: Path) -> None:
    """Only the file's existence was checked, so a Look with a broken LUT
    failed every export inside the render. Resolving reads it, so the
    request layer can make it a note."""
    root = _look_with(user_looks / "bad", {"lut": "luts/mine.cube"}, lut=True)
    (root / "luts" / "mine.cube").write_text("LUT_3D_SIZE 2\n0 0 x\n", encoding="utf-8")
    with pytest.raises(GradeError):
        grade.resolve_grade("look", looks.load_look("bad"))


def test_strength_zero_is_no_grade(user_looks: Path) -> None:
    assert grade.resolve_grade("preset:noir", looks.load_look("splitsmith"), strength=0.0) is None


def test_lut_add_follows_a_link_and_checks_size_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grade, "user_luts_dir", lambda: tmp_path / "luts")
    real = tmp_path / "real.cube"
    real.write_text(_cube(2, _identity_rows(2)), encoding="utf-8")
    link = tmp_path / "film.cube"
    link.symlink_to(real)
    assert grade.add_user_lut(link).startswith("film-")
    monkeypatch.setattr(grade, "MAX_BYTES", 10)
    with pytest.raises(GradeError, match="over"):
        grade.add_user_lut(real)
