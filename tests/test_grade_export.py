"""Choosing a colour grade for a match export: the Look's own by default, a
preset, your own LUT, or none; resolved by the request layer."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from splitsmith import grade, looks
from splitsmith.cli import app

from .test_match_cli_export import _capture_mp4, _seed

runner = CliRunner()


@pytest.fixture
def user_looks(tmp_path: Path):
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    yield tmp_path / "looks"
    looks.reset_user_looks_provider(token)


def _graded_look(root: Path, grade_block: object) -> None:
    shutil.copytree(looks.shipped_looks_dir() / "splitsmith", root)
    manifest = json.loads((root / "look.json").read_text())
    manifest["name"] = root.name
    manifest["grade"] = grade_block
    (root / "look.json").write_text(json.dumps(manifest))


def _export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> tuple[Any, Any]:
    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    out = tmp_path / "out" / "lt.mp4"
    result = runner.invoke(
        app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "-o", str(out), *args]
    )
    return result, captured.get("comp")


def test_no_grade_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, comp = _export(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert comp.grade is None


def test_a_preset_and_its_strength_reach_the_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, comp = _export(tmp_path, monkeypatch, "--grade", "preset:noir", "--grade-strength", "0.4")
    assert result.exit_code == 0, result.output
    assert comp.grade.source == grade.preset_path("noir") and comp.grade.strength == 0.4


def test_the_looks_own_grade_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_looks: Path
) -> None:
    _graded_look(user_looks / "graded", {"preset": "warm-film", "strength": 0.7})
    result, comp = _export(tmp_path, monkeypatch, "--theme", "graded")
    assert result.exit_code == 0, result.output
    assert comp.grade.source == grade.preset_path("warm-film") and comp.grade.strength == 0.7
    none_result, ungraded = _export(tmp_path / "again", monkeypatch, "--theme", "graded", "--grade", "none")
    assert none_result.exit_code == 0 and ungraded.grade is None


def test_an_explicit_grade_naming_nothing_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, comp = _export(tmp_path, monkeypatch, "--grade", "preset:nope")
    assert result.exit_code != 0 and comp is None
    assert "no grade preset named 'nope'" in result.output


def test_a_looks_broken_grade_is_a_note_and_the_render_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_looks: Path
) -> None:
    _graded_look(user_looks / "broken", {"lut": "luts/missing.cube"})
    result, comp = _export(tmp_path, monkeypatch, "--theme", "broken")
    assert result.exit_code == 0, result.output
    assert comp.grade is None
    assert "colour grade left out" in result.output


def test_a_timeline_format_says_the_grade_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_match_cli_export import _meta

    root = _seed(tmp_path)
    monkeypatch.setattr(match_exports_mod.fcpxml_gen, "probe_video", _meta)
    result = runner.invoke(
        app,
        ["match", "export", str(root), "--shooter", "me", "--format", "fcpxml", "--grade", "preset:vivid"],
    )
    assert result.exit_code == 0, result.output
    assert "colour grade ignored" in result.output


def test_the_thumbnail_frame_is_graded(tmp_path: Path) -> None:
    from splitsmith.thumbnail_card import grab_frame

    seen: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> None:
        seen.append(argv)
        Path(argv[-1]).write_bytes(b"png")

    lut = tmp_path / "grade-abc.cube"
    grab_frame(tmp_path / "v.mp4", 1.0, tmp_path / "f.png", runner=run, grade_lut=lut)
    assert grade.lut_filter(lut) in seen[0]
    grab_frame(tmp_path / "v.mp4", 1.0, tmp_path / "g.png", runner=run)
    assert not any("lut3d" in token for token in seen[1])


def test_grade_options_default_everywhere_a_user_starts() -> None:
    from splitsmith.export_presets import ExportPresetBody
    from splitsmith.ui.exports_api import MatchExportRequest
    from splitsmith.ui.match_exports import MatchExportRequestData

    assert ExportPresetBody().grade == "look" and ExportPresetBody().grade_strength is None
    assert MatchExportRequest(stage_numbers=[1]).grade == "look"
    assert MatchExportRequestData.__dataclass_fields__["grade"].default == "look"
    with pytest.raises(ValueError):
        MatchExportRequest(stage_numbers=[1], grade="preset:../x")
    with pytest.raises(ValueError):
        MatchExportRequest(stage_numbers=[1], grade_strength=1.5)
