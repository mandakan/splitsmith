"""Trim-only planning and execution across a match's shooters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import ValidationError

from splitsmith import camera_select, match_trims
from splitsmith.match_model import Match
from splitsmith.ui.project import MatchProject
from tests.conftest import _video

# ``two_shooter_match`` lives in tests/conftest.py so ``test_match_trims_cli.py``
# can reuse it (pytest fixtures are auto-discovered; the plain ``_video`` helper
# is not, hence the explicit import above).


def _literal_values(annotation: Any) -> set[str]:
    """Flatten a ``Literal[...]`` -- or a union of them -- to its strings."""
    if get_origin(annotation) is Literal:
        return set(get_args(annotation))
    return {v for arg in get_args(annotation) for v in _literal_values(arg)}


def _find(plan: list[match_trims.TrimPlanEntry], slug: str, stage: int) -> match_trims.TrimPlanEntry:
    """Return the single plan entry for ``slug`` on ``stage``."""
    matches = [e for e in plan if e.shooter_slug == slug and e.stage_number == stage]
    assert len(matches) == 1, f"expected exactly one entry for {slug}/{stage}, got {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# plan_trims
# ---------------------------------------------------------------------------


def test_plan_marks_stage_without_beep_ineligible(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match)
    entry = _find(plan, "anders", 2)
    assert entry.eligible is False
    assert entry.reason == "no_beep"


def test_plan_marks_skipped_stage_ineligible(two_shooter_match: Path) -> None:
    entry = _find(match_trims.plan_trims(two_shooter_match), "anders", 3)
    assert entry.eligible is False
    assert entry.reason == "skipped"


def test_plan_marks_stage_without_time_ineligible(two_shooter_match: Path) -> None:
    """Trim length is beep-anchored but sized by the stage time -- no time,
    no trim. Guessing a duration pads the grid for every shooter."""
    plan = match_trims.plan_trims(two_shooter_match)
    entry = _find(plan, "mathias", 3)
    assert entry.eligible is False
    assert entry.reason == "no_stage_time"


def test_plan_marks_unreachable_source_ineligible(two_shooter_match: Path) -> None:
    """The project stores paths into raw/; an unplugged drive is a skip, not a crash."""
    (Match.shooter_root(two_shooter_match, "anders") / "raw" / "a1.mov").unlink()
    entry = _find(match_trims.plan_trims(two_shooter_match), "anders", 1)
    assert entry.eligible is False
    assert entry.reason == "source_unreachable"


def test_plan_skips_existing_trims_unless_forced(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match)
    assert _find(plan, "mathias", 1).reason == "already_exported"
    forced = match_trims.plan_trims(two_shooter_match, force=True)
    assert _find(forced, "mathias", 1).eligible is True


def test_plan_honours_shooter_and_stage_filters(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match, shooters=["anders"], stages=[1])
    assert {(e.shooter_slug, e.stage_number) for e in plan} == {("anders", 1)}


def test_plan_records_camera_substitution(two_shooter_match: Path) -> None:
    """Anders is on 'chest' but stage 1 has no chest cam."""
    plan = match_trims.plan_trims(two_shooter_match, cameras={"anders": "chest"})
    entry = _find(plan, "anders", 1)
    assert entry.eligible is True
    assert entry.substituted_from == "chest"


def test_plan_uses_persisted_compare_camera(two_shooter_match: Path) -> None:
    """``MatchProject.compare_camera`` is the default when no override is passed."""
    anders = Match.shooter_root(two_shooter_match, "anders")
    project = MatchProject.load(anders)
    project.compare_camera = "chest"
    project.save(anders)

    entry = _find(match_trims.plan_trims(two_shooter_match), "anders", 1)
    assert entry.camera == "chest"
    assert entry.substituted_from == "chest"


def test_plan_reports_ambiguous_camera_as_ineligible(two_shooter_match: Path) -> None:
    """Two secondaries on one stage must cost the user that stage, not the run."""
    plan = match_trims.plan_trims(two_shooter_match, cameras={"mathias": "secondary"})
    entry = _find(plan, "mathias", 3)
    assert entry.eligible is False
    assert entry.reason == "camera_ambiguous"
    # The rest of the shooter still got classified.
    assert _find(plan, "mathias", 2).eligible is True


def test_plan_raises_for_camera_matching_nothing(two_shooter_match: Path) -> None:
    """A whole-project typo is a config error the CLI turns into exit 2."""
    with pytest.raises(camera_select.CameraResolutionError):
        match_trims.plan_trims(two_shooter_match, cameras={"anders": "nosuchmount"})


def test_plan_touches_no_media(two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """plan_trims must be pure: no ffmpeg, no probing."""

    def explode(*_a: object, **_kw: object) -> None:
        raise AssertionError("plan_trims must not touch media")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", explode)
    match_trims.plan_trims(two_shooter_match)


# ---------------------------------------------------------------------------
# Shared eligibility rule (#613) and typed skip reasons (#614)
# ---------------------------------------------------------------------------


def test_plan_agrees_with_export_overview_on_trim_eligibility(two_shooter_match: Path) -> None:
    """The planner and the SPA's overview must classify the same stage the
    same way -- one rule, two surfaces (#613).

    ``ready_to_trim`` covers only the permanent blockers (skipped / no beep /
    no stage time). A stage the planner turns down for a *transient* reason
    (the trim is already on disk, the drive is unplugged) is still
    trim-ready, so the SPA keeps offering it.
    """
    transient = {"already_exported", "source_unreachable", "camera_ambiguous"}
    plan = match_trims.plan_trims(two_shooter_match)
    for slug in ("anders", "mathias"):
        shooter_root = Match.shooter_root(two_shooter_match, slug)
        project = MatchProject.load(shooter_root)
        for row in project.export_overview(shooter_root):
            entry = _find(plan, slug, row.stage_number)
            if row.ready_to_trim:
                assert entry.eligible or entry.reason in transient, (
                    f"{slug}/{row.stage_number}: overview says trim-ready, " f"planner says {entry.reason}"
                )
            else:
                assert not entry.eligible
                assert entry.reason not in transient


def test_stage_time_alone_makes_a_stage_trim_ready(two_shooter_match: Path) -> None:
    """A scoreboard row that carried a time but an unparseable
    ``scorecard_updated_at`` leaves ``time_seconds > 0`` with neither
    ``scorecard_updated_at`` nor ``time_seconds_manual`` set. The trim is
    cuttable from a beep plus a duration, so every surface must accept it.
    """
    shooter_root = Match.shooter_root(two_shooter_match, "mathias")
    project = MatchProject.load(shooter_root)
    stage = project.stages[0]
    assert stage.scorecard_updated_at is None
    assert stage.time_seconds_manual is False

    row = next(r for r in project.export_overview(shooter_root) if r.stage_number == 1)
    assert row.ready_to_trim is True


def test_skip_reason_is_a_closed_set(two_shooter_match: Path) -> None:
    """A typo'd reason must not validate (#614) -- the CLI's exit code keys
    off these strings, so a silent typo silently changes the exit code."""
    with pytest.raises(ValidationError):
        match_trims.TrimPlanEntry(
            shooter_slug="mathias",
            stage_number=1,
            stage_name="Egg Grab",
            reason="allready_exported",  # type: ignore[arg-type]
        )


def test_cli_satisfied_reasons_are_real_skip_reasons() -> None:
    """The exit-code rule's "not outstanding work" set must be drawn from
    the same Literal, so a rename can't leave the CLI matching on a string
    nothing produces any more."""
    from splitsmith import match_cli

    assert set(match_cli.SATISFIED_REASONS) <= _literal_values(match_trims.SkipReason)


# ---------------------------------------------------------------------------
# run_trims
# ---------------------------------------------------------------------------


def _fake_trim_video(written: list[Path]):
    def fake_trim_video(src: Path, dst: Path, **kwargs: object) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")
        written.append(dst)

    return fake_trim_video


def test_run_trims_writes_only_eligible_stages(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Path] = []
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video(written))

    plan = match_trims.plan_trims(two_shooter_match)
    results = match_trims.run_trims(two_shooter_match, plan)

    assert {r.entry.stage_number for r in results if r.trim_path} == {1, 2}
    assert all(p.name.endswith("_trimmed.mp4") for p in written)
    # A clean trim-only export reports nothing: "no shots audited" is the
    # designed state here, not a problem worth showing the user.
    assert [r.skip_reasons for r in results if r.trim_path] == [[], []]
    # Ineligible entries come back with the plan's reason, not silence.
    ineligible = [r for r in results if not r.entry.eligible]
    assert all(r.skip_reasons for r in ineligible)


def test_run_trims_reports_progress_for_each_export(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video([]))
    seen: list[tuple[str, int]] = []

    plan = match_trims.plan_trims(two_shooter_match)
    match_trims.run_trims(
        two_shooter_match, plan, progress=lambda e: seen.append((e.shooter_slug, e.stage_number))
    )

    assert seen == [("anders", 1), ("mathias", 2)]


def test_run_trims_exports_selected_secondary_camera(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chest-cam run writes the per-cam trim, not the primary's."""
    written: list[Path] = []
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video(written))

    plan = match_trims.plan_trims(
        two_shooter_match, shooters=["mathias"], stages=[2], cameras={"mathias": "chest"}
    )
    assert _find(plan, "mathias", 2).eligible is True
    results = match_trims.run_trims(two_shooter_match, plan)

    assert len(written) == 1
    assert "_cam_" in written[0].name
    assert results[0].trim_path == written[0]
    # The primary's trim is deliberately absent on a secondary-cam run;
    # don't report that as a failure.
    assert results[0].skip_reasons == []


def test_run_trims_reports_ffmpeg_failure_without_aborting(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad stage must not cost the user the other twenty-three."""
    calls = {"n": 0}

    def flaky_trim_video(src: Path, dst: Path, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise match_trims.exports.trim.FFmpegError("boom")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", flaky_trim_video)

    plan = match_trims.plan_trims(two_shooter_match)
    results = match_trims.run_trims(two_shooter_match, plan)

    failed = [r for r in results if r.entry.eligible and r.trim_path is None]
    assert len(failed) == 1
    assert any("boom" in reason for reason in failed[0].skip_reasons)
    assert any(r.trim_path is not None for r in results)


def test_run_trims_survives_a_stage_deleted_after_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-loading the project at run time can find the stage gone.

    ``run_trims``'s promise is that one stage's failure never ends the run,
    so a stage the user deleted between --dry-run and the run is that
    stage's skip -- the rest of the match still exports.
    """
    written: list[Path] = []
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video(written))

    plan = match_trims.plan_trims(two_shooter_match)
    assert _find(plan, "anders", 1).eligible is True

    anders = Match.shooter_root(two_shooter_match, "anders")
    project = MatchProject.load(anders)
    project.stages = [s for s in project.stages if s.stage_number != 1]
    project.save(anders)

    results = match_trims.run_trims(two_shooter_match, plan)

    gone = next(r for r in results if r.entry.shooter_slug == "anders" and r.entry.stage_number == 1)
    assert gone.trim_path is None
    assert any("no stage 1" in reason for reason in gone.skip_reasons)
    # The other shooter's eligible stage still ran.
    assert [r.entry.stage_number for r in results if r.trim_path] == [2]


def test_run_trims_survives_an_unloadable_project_after_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project.json that stops parsing is one shooter's problem, not the run's."""
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video([]))

    plan = match_trims.plan_trims(two_shooter_match)
    anders = Match.shooter_root(two_shooter_match, "anders")
    (anders / "project.json").write_text("{ not json", encoding="utf-8")

    results = match_trims.run_trims(two_shooter_match, plan)

    broken = next(r for r in results if r.entry.shooter_slug == "anders" and r.entry.stage_number == 1)
    assert broken.trim_path is None
    assert broken.skip_reasons
    assert any(r.trim_path is not None for r in results)


def test_run_trims_records_substitution_that_changed_since_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan said "chest missing, using primary"; by run time the chest cam
    is there. The run exports a different camera than --dry-run showed, so it
    has to say so rather than swap angles silently."""
    written: list[Path] = []
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video(written))

    plan = match_trims.plan_trims(
        two_shooter_match, shooters=["anders"], stages=[1], cameras={"anders": "chest"}
    )
    assert _find(plan, "anders", 1).substituted_from == "chest"

    anders = Match.shooter_root(two_shooter_match, "anders")
    project = MatchProject.load(anders)
    project.stage(1).videos.append(
        _video(anders, "raw/a1_chest.mov", role="secondary", beep_time=4.0, camera_mount="chest")
    )
    project.save(anders)

    results = match_trims.run_trims(two_shooter_match, plan)

    assert results[0].trim_path is not None
    assert "_cam_" in results[0].trim_path.name
    assert any("chest" in reason for reason in results[0].skip_reasons)


def test_run_trims_survives_camera_ambiguity_appearing_after_the_plan(
    two_shooter_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project can change between --dry-run and the real run."""
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video([]))

    plan = match_trims.plan_trims(
        two_shooter_match, shooters=["mathias"], stages=[2], cameras={"mathias": "secondary"}
    )
    assert _find(plan, "mathias", 2).eligible is True

    mathias = Match.shooter_root(two_shooter_match, "mathias")
    project = MatchProject.load(mathias)
    project.stage(2).videos.append(_video(mathias, "raw/m2_second.mov", role="secondary", beep_time=4.0))
    project.save(mathias)

    results = match_trims.run_trims(two_shooter_match, plan)

    assert results[0].trim_path is None
    assert results[0].skip_reasons


def test_plan_names_trims_from_the_shooters_own_stage_name(two_shooter_match: Path) -> None:
    """``already_exported`` must recognise a trim written under the shooter's name.

    ``match.json`` and ``project.json`` agree right after ``match merge``
    (which validates stage-definition consistency), but a per-shooter
    scoreboard import rewrites ``project.stages`` and leaves the match
    alone. Planning against the match name then misses a trim the SPA
    already wrote and re-cuts it under a second filename.
    """
    mathias = Match.shooter_root(two_shooter_match, "mathias")
    project = MatchProject.load(mathias)
    project.stage(2).stage_name = "El Presidente"
    project.save(mathias)

    exports = project.exports_path(mathias)
    (exports / "stage2_el-presidente_trimmed.mp4").write_bytes(b"written by the SPA")

    entry = _find(match_trims.plan_trims(two_shooter_match), "mathias", 2)
    assert entry.stage_name == "El Presidente"
    assert entry.reason == "already_exported"
