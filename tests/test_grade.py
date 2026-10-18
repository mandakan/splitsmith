"""Colour grades: 3D LUTs read from ``.cube`` files, blended by strength and
written content-named where the render reads them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from splitsmith import grade, looks
from splitsmith.grade import GradeError, Lut, ResolvedGrade


def _cube(size: int, rows: list[str], header: str = "") -> str:
    return f'TITLE "t"\n{header}LUT_3D_SIZE {size}\n' + "\n".join(rows) + "\n"


def _identity_rows(size: int) -> list[str]:
    step = 1 / (size - 1)
    return [
        f"{r * step:.6f} {g * step:.6f} {b * step:.6f}"
        for b in range(size)
        for g in range(size)
        for r in range(size)
    ]


def test_a_cube_reads_red_fastest_and_round_trips() -> None:
    lut = grade.parse_cube(_cube(2, _identity_rows(2)))
    assert lut.size == 2
    # [b][g][r]: red varies fastest in the file.
    assert lut.table[0, 0, 1].tolist() == [1.0, 0.0, 0.0]
    assert lut.table[1, 0, 0].tolist() == [0.0, 0.0, 1.0]
    again = grade.parse_cube(grade.write_cube(lut))
    assert np.allclose(again.table, lut.table)
    assert np.allclose(grade.identity(2).table, lut.table)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("LUT_1D_SIZE 4\n0 0 0\n", "3D"),
        (_cube(2, _identity_rows(2)[:-1]), "8 rows"),
        (_cube(2, ["0 0 x"] + _identity_rows(2)[1:]), "number"),
        (_cube(2, _identity_rows(2), header="DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n"), "0..1 input range"),
        (_cube(70, []), "size"),
        ("0 0 0\n", "LUT_3D_SIZE"),
    ],
)
def test_a_malformed_cube_is_refused_saying_why(text: str, match: str) -> None:
    with pytest.raises(GradeError, match=match):
        grade.parse_cube(text)


def test_strength_blends_toward_the_identity() -> None:
    inverted = Lut(size=2, table=1.0 - grade.identity(2).table)
    assert np.allclose(grade.blend(inverted, 0.0).table, grade.identity(2).table)
    assert np.allclose(grade.blend(inverted, 1.0).table, inverted.table)
    assert np.allclose(grade.blend(inverted, 0.5).table, 0.5)


def test_the_render_file_is_named_by_what_it_draws(tmp_path: Path) -> None:
    """The render's ffmpeg argv names the file, so its name must move with
    its content: the segment cache keys on the argv (``rendering.md``)."""
    source = tmp_path / "warm.cube"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)))
    full = grade.materialize(ResolvedGrade(source=source, strength=1.0, label="warm"), tmp_path / "work")
    half = grade.materialize(ResolvedGrade(source=source, strength=0.5, label="warm"), tmp_path / "work")
    again = grade.materialize(ResolvedGrade(source=source, strength=1.0, label="warm"), tmp_path / "work")
    assert full == again and full != half
    assert full.name.startswith("grade-") and full.suffix == ".cube"
    assert grade.parse_cube(full.read_text()).size == 2


def test_the_filter_reads_the_file_with_tetrahedral_interpolation(tmp_path: Path) -> None:
    assert grade.lut_filter(Path("/w/grade-abc.cube")) == "lut3d=file='/w/grade-abc.cube':interp=tetrahedral"


def test_every_shipped_preset_is_a_valid_cube() -> None:
    names = grade.preset_names()
    assert {"vivid", "warm-film", "teal-orange", "cool-steel", "bleach-bypass", "faded", "noir"} <= set(names)
    for name in names:
        lut = grade.parse_cube(grade.preset_path(name).read_text())
        assert lut.size == 17 and float(lut.table.min()) >= 0.0 and float(lut.table.max()) <= 1.0


def _look_with(root: Path, grade_block: object, *, lut: bool = False) -> Path:
    shutil.copytree(looks.shipped_looks_dir() / "splitsmith", root)
    manifest = json.loads((root / "look.json").read_text())
    manifest["name"] = root.name
    manifest["grade"] = grade_block
    (root / "look.json").write_text(json.dumps(manifest))
    if lut:
        (root / "luts").mkdir()
        (root / "luts" / "mine.cube").write_text(_cube(2, _identity_rows(2)))
    return root


@pytest.fixture
def user_looks(tmp_path: Path):
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    yield tmp_path / "looks"
    looks.reset_user_looks_provider(token)


def test_a_looks_grade_is_its_lut_or_a_preset(user_looks: Path) -> None:
    _look_with(user_looks / "own", {"lut": "luts/mine.cube", "strength": 0.8}, lut=True)
    _look_with(user_looks / "named", {"preset": "warm-film"})
    own = grade.resolve_grade("look", looks.load_look("own"))
    assert own is not None and own.source == user_looks / "own" / "luts" / "mine.cube" and own.strength == 0.8
    named = grade.resolve_grade("look", looks.load_look("named"))
    assert named is not None and named.source == grade.preset_path("warm-film") and named.strength == 1.0
    assert grade.resolve_grade("look", looks.load_look("splitsmith")) is None
    assert grade.resolve_grade("none", looks.load_look("own")) is None


def test_an_explicit_choice_and_strength_win_over_the_looks(user_looks: Path) -> None:
    _look_with(user_looks / "own", {"lut": "luts/mine.cube", "strength": 0.8}, lut=True)
    chosen = grade.resolve_grade("preset:noir", looks.load_look("own"), strength=0.3)
    assert chosen is not None and chosen.source == grade.preset_path("noir") and chosen.strength == 0.3


@pytest.mark.parametrize(
    "choice",
    ["preset:../../etc/passwd", "preset:nope", "user:../x.cube", "user:missing-0123456789ab.cube", "bogus"],
)
def test_a_choice_naming_nothing_real_is_refused(user_looks: Path, choice: str) -> None:
    with pytest.raises(GradeError):
        grade.resolve_grade(choice, looks.load_look("splitsmith"))


def test_a_looks_lut_outside_its_folder_is_refused(user_looks: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.cube").write_text(_cube(2, _identity_rows(2)))
    _look_with(user_looks / "sly", {"lut": "../../outside.cube"})
    with pytest.raises(GradeError, match="Look's folder"):
        grade.resolve_grade("look", looks.load_look("sly"))


def test_your_own_lut_is_checked_and_kept_content_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grade, "user_luts_dir", lambda: tmp_path / "luts")
    source = tmp_path / "My Film Look.CUBE"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)))
    name = grade.add_user_lut(source)
    assert grade.USER_LUT_RE.match(name) and name.startswith("my-film-look-")
    assert (tmp_path / "luts" / name).is_file()
    assert grade.add_user_lut(source) == name, "the same bytes, the same file"
    assert grade.list_user_luts() == [name]
    resolved = grade.resolve_grade(f"user:{name}", looks.load_look("splitsmith"))
    assert resolved is not None and resolved.source == tmp_path / "luts" / name

    broken = tmp_path / "broken.cube"
    broken.write_text("LUT_1D_SIZE 4\n")
    with pytest.raises(GradeError, match="3D"):
        grade.add_user_lut(broken)
    assert grade.list_user_luts() == [name], "a refused file is never kept"


def test_lut_add_on_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from splitsmith.cli import app

    monkeypatch.setattr(grade, "user_luts_dir", lambda: tmp_path / "luts")
    source = tmp_path / "film.cube"
    source.write_text(_cube(2, _identity_rows(2)))
    result = CliRunner().invoke(app, ["looks", "lut-add", str(source)])
    assert result.exit_code == 0, result.output
    assert "--grade user:film-" in result.output
    bad = tmp_path / "bad.cube"
    bad.write_text("nonsense\n")
    assert CliRunner().invoke(app, ["looks", "lut-add", str(bad)]).exit_code == 2


def test_the_catalog_says_which_looks_carry_a_grade(user_looks: Path) -> None:
    _look_with(user_looks / "named", {"preset": "warm-film"})
    by_name = {info.name: info for info in looks.look_catalog()}
    assert by_name["named"].grade is True and by_name["splitsmith"].grade is False
