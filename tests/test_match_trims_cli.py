"""CLI tests for ``splitsmith match trims``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from splitsmith import match_trims
from splitsmith.cli import app
from splitsmith.match_model import Match, MatchStageDefinition
from splitsmith.match_project import MatchProject, StageEntry
from tests.conftest import _video

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


def test_camera_flag_accepts_a_display_name(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``match trims`` and ``compare export`` are documented as a chain, so a
    shooter spelled by display name in one must work in the other (#618)."""
    from splitsmith.match_model import Shooter

    # The fixture registers the slug but writes no shooter.json; the display
    # name lives in that file, so give it one.
    Shooter(slug="anders", name="Anders Bengtsson").save(Match.shooter_root(two_shooter_match, "anders"))

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    result = runner.invoke(
        app,
        ["match", "trims", str(two_shooter_match), "--camera", "Anders Bengtsson=chest"],
    )
    assert result.exit_code == 0, result.output
    # The override reached the planner: anders' stage 1 has no chest cam, so
    # it substitutes and the Camera column says so.
    assert "chest -> primary" in " ".join(result.stdout.split())


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


def _run_with_a_stale_plan(match_root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Drive ``match trims`` with a plan computed before the project changed.

    The real divergence: ``--dry-run`` (or the plan pass) sees no chest cam
    and records a substitution, the user plugs in the cam, then the run
    exports the chest angle instead. The command re-plans internally, so the
    only way to model a stale plan is to hand it one -- which is exactly what
    a user does by planning, editing, then running.
    """
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))

    stale = match_trims.plan_trims(match_root, shooters=["anders"], stages=[1], cameras={"anders": "chest"})
    assert stale[0].substituted_from == "chest", "fixture no longer sets up a substitution"

    anders = Match.shooter_root(match_root, "anders")
    project = MatchProject.load(anders)
    project.stage(1).videos.append(
        _video(anders, "raw/a1_chest.mov", role="secondary", beep_time=4.0, camera_mount="chest")
    )
    project.save(anders)

    monkeypatch.setattr(match_trims, "plan_trims", lambda *_a, **_kw: stale)
    result = runner.invoke(
        app,
        [
            "match",
            "trims",
            str(match_root),
            "--shooter",
            "anders",
            "--stage",
            "1",
            "--camera",
            "anders=chest",
        ],
    )
    assert result.exit_code == 0, result.output
    return " ".join(result.stdout.split())  # rich wraps the table column


def test_camera_divergence_reaches_the_user(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of #617: the run exported a different angle than the
    plan showed, and the user has to be able to see that.

    The datum existed and was tested before this -- it just never reached
    the screen, because the Status column short-circuited on "written" and
    the note was filed under ``skip_reasons`` on a successful export.
    """
    out = _run_with_a_stale_plan(two_shooter_match, monkeypatch)
    # The row is flagged...
    assert "see note" in out
    # ...and the note itself is printed in full, under the table, where rich
    # cannot ellipsize it into uselessness.
    assert "camera substitution changed since planning" in out
    assert "planned chest, ran none" in out
    assert "anders stage 1" in out


def test_substitution_count_describes_the_run_not_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan expected to substitute; the run did not, because the cam was
    there by then. The summary describes the run, so it counts zero."""
    out = _run_with_a_stale_plan(two_shooter_match, monkeypatch)
    assert "0 substitutions" in out
    # And so does the Camera column: this row shipped the chest angle, so
    # claiming "chest -> primary" would be a straight falsehood.
    assert "chest -> primary" not in out


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


def _fully_exportable_match(tmp_path: Path) -> Path:
    """One shooter, one exportable stage plus one deliberately skipped stage.

    No permanently-ineligible reason (no_beep, no_stage_time, ...) appears,
    so once the exportable stage has a trim, every remaining entry is either
    ``already_exported`` or ``skipped`` -- a match that is genuinely done.
    """
    match_root = tmp_path / "done_match"
    match = Match.init(match_root, name="Done Match")
    match.stages = [
        MatchStageDefinition(stage_number=1, stage_name="Only Real Stage"),
        MatchStageDefinition(stage_number=2, stage_name="Skipped Stage"),
    ]
    match.shooters = ["solo"]
    match.save(match_root)

    shooter_root = Match.shooter_root(match_root, "solo")
    project = MatchProject.init(shooter_root, name="Done Match")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Only Real Stage",
            time_seconds=10.0,
            videos=[_video(shooter_root, "raw/a.mov")],
        ),
        StageEntry(stage_number=2, stage_name="Skipped Stage", time_seconds=10.0, skipped=True),
    ]
    project.save(shooter_root)
    return match_root


def test_rerun_of_fully_exported_match_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running against a match that's already fully exported must not fail --
    ``match trims <match> && compare export <match> ...`` needs to chain safely
    even when the second run writes nothing because everything is already done."""
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t"))
    match_root = _fully_exportable_match(tmp_path)

    first = runner.invoke(app, ["match", "trims", str(match_root)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["match", "trims", str(match_root)])
    assert second.exit_code == 0, second.output
    assert "already_exported" in second.stdout


def test_no_beep_and_no_stage_time_still_exit_1(tmp_path: Path) -> None:
    """The outstanding-work fix must not turn every zero-write run green --
    stages that never got a trim (no_beep, no_stage_time) are still a failure."""
    match_root = tmp_path / "no_work"
    match = Match.init(match_root, name="No Work")
    match.stages = [
        MatchStageDefinition(stage_number=1, stage_name="Stage A"),
        MatchStageDefinition(stage_number=2, stage_name="Stage B"),
    ]
    match.shooters = ["solo"]
    match.save(match_root)

    shooter_root = Match.shooter_root(match_root, "solo")
    project = MatchProject.init(shooter_root, name="No Work")
    project.stages = [
        StageEntry(stage_number=1, stage_name="Stage A", time_seconds=10.0, videos=[]),
        StageEntry(
            stage_number=2,
            stage_name="Stage B",
            time_seconds=0.0,
            videos=[_video(shooter_root, "raw/b.mov")],
        ),
    ]
    project.save(shooter_root)

    result = runner.invoke(app, ["match", "trims", str(match_root)])
    assert result.exit_code == 1, result.output
    assert "no_beep" in result.stdout
    assert "no_stage_time" in result.stdout
