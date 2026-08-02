"""CLI tests for ``splitsmith match trims``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from splitsmith import match_trims
from splitsmith.cli import app

runner = CliRunner()


def test_dry_run_prints_plan_and_writes_nothing(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_a: object, **_kw: object) -> None:
        raise AssertionError("--dry-run must not write")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", explode)
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "no_stage_time" in result.stdout
    assert "no_beep" in result.stdout


def test_reports_camera_substitution(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--camera", "anders=chest"])

    assert result.exit_code == 0, result.output
    assert "chest -> primary" in result.stdout


def test_exit_code_1_when_nothing_written(empty_match: Path) -> None:
    """A match where every stage is ineligible is a failed run, not a no-op."""
    result = runner.invoke(app, ["match", "trims", str(empty_match)])
    assert result.exit_code == 1


def test_partial_run_exits_zero(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.stdout.lower()


def test_bad_camera_pair_exits_2(two_shooter_match: Path) -> None:
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--camera", "nonsense"])
    assert result.exit_code == 2
    assert "SLUG=VALUE" in result.output


def test_unknown_camera_slug_exits_2(two_shooter_match: Path) -> None:
    """A typo'd shooter slug would otherwise export the default camera and look
    like it worked -- mirrors ``compare export``'s guard for the same mistake."""
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--camera", "notashooter=chest"])
    assert result.exit_code == 2
    assert "notashooter" in result.output
    assert "anders" in result.output
    assert "mathias" in result.output


def test_unresolvable_camera_exits_2(two_shooter_match: Path) -> None:
    """A selector matching no mount or role anywhere is a config error, not a traceback."""
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--camera", "anders=backpack"])
    assert result.exit_code == 2
    assert "backpack" in result.output


def test_not_a_match_folder_exits_2(tmp_path: Path) -> None:
    not_a_match = tmp_path / "plain"
    not_a_match.mkdir()
    result = runner.invoke(app, ["match", "trims", str(not_a_match)])
    assert result.exit_code == 2


def test_camera_ambiguous_reason_rendered(two_shooter_match: Path) -> None:
    """The seventh skip reason (camera_ambiguous) must render, not blank out."""
    result = runner.invoke(
        app, ["match", "trims", str(two_shooter_match), "--dry-run", "--camera", "mathias=secondary"]
    )
    assert result.exit_code == 0, result.output
    assert "camera_ambiguous" in result.stdout


def test_shooter_and_stage_filters_narrow_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    result = runner.invoke(
        app,
        [
            "match",
            "trims",
            str(two_shooter_match),
            "--shooter",
            "anders",
            "--stage",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "mathias" not in result.stdout


def test_force_recuts_already_exported(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--force"])
    assert result.exit_code == 0, result.output
    assert "already_exported" not in result.stdout
