"""Tests for the match-as-object data model (issue #320)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from splitsmith.config import StageRounds
from splitsmith.match_model import (
    MATCH_FILE,
    SHOOTER_FILE,
    SHOOTERS_DIR,
    Match,
    MatchStageDefinition,
    MergeConflictError,
    Shooter,
    ShooterStageData,
    disambiguate_slug,
    execute_merge,
    from_path,
    is_legacy_project_folder,
    is_match_folder,
    legacy_to_match_view,
    load_match_or_legacy,
    plan_merge,
    slugify,
)
from splitsmith.ui.project import MatchProject, StageEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_legacy(
    root: Path,
    *,
    name: str,
    competitor: str,
    scoreboard_id: str | None = "27242",
    content_type: int | None = 22,
    match_date: date | None = date(2026, 4, 3),
    shooter_id: int | None = 55429,
    competitor_id: int | None = 731843,
    stage_names: list[str] | None = None,
    stage_rounds_for_first: StageRounds | None = None,
) -> MatchProject:
    """Create a minimal legacy single-shooter project on disk for tests."""
    project = MatchProject.init(root, name=name)
    project.competitor_name = competitor
    project.scoreboard_match_id = scoreboard_id
    project.scoreboard_content_type = content_type
    project.match_date = match_date
    project.selected_shooter_id = shooter_id
    project.selected_competitor_id = competitor_id
    names = stage_names or ["Egg Grab", "Long Friday", "Tower"]
    for i, sn in enumerate(names, start=1):
        rounds = stage_rounds_for_first if i == 1 else None
        project.stages.append(
            StageEntry(
                stage_number=i,
                stage_name=sn,
                time_seconds=10.0 + i,
                stage_rounds=rounds,
            )
        )
    project.save(root)
    return project


# ---------------------------------------------------------------------------
# slug helpers
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert slugify("Mathias Axell") == "mathias-axell"


def test_slugify_handles_accents():
    assert slugify("Martin Engström") == "martin-engstrom"


def test_slugify_collapses_punctuation_and_strips_edges():
    assert slugify("  Anders! Andersson??  ") == "anders-andersson"


def test_slugify_empty_falls_back():
    assert slugify("") == "shooter"
    assert slugify("---") == "shooter"


def test_disambiguate_slug():
    taken = {"anton", "anton-2"}
    assert disambiguate_slug("anton", taken) == "anton-3"
    assert disambiguate_slug("anton", set()) == "anton"


# ---------------------------------------------------------------------------
# Schema round-trips
# ---------------------------------------------------------------------------


def test_match_round_trip(tmp_path: Path):
    root = tmp_path / "match"
    match = Match.init(root, name="VADS Easter Shoot 2026")
    match.scoreboard_match_id = "27242"
    match.scoreboard_content_type = 22
    match.match_date = date(2026, 4, 3)
    match.stages = [
        MatchStageDefinition(stage_number=1, stage_name="Egg Grab"),
        MatchStageDefinition(stage_number=2, stage_name="Tower"),
    ]
    match.save(root)

    reloaded = Match.load(root)
    assert reloaded.name == "VADS Easter Shoot 2026"
    assert reloaded.scoreboard_match_id == "27242"
    assert len(reloaded.stages) == 2
    assert reloaded.stages[0].stage_name == "Egg Grab"


def test_shooter_round_trip(tmp_path: Path):
    match = Match.init(tmp_path / "match", name="Test")
    shooter = Shooter(slug="mathias", name="Mathias Axell", selected_shooter_id=42)
    shooter.stages = [
        ShooterStageData(stage_number=1, time_seconds=12.3),
        ShooterStageData(stage_number=2, time_seconds=8.45),
    ]
    match.add_shooter(tmp_path / "match", shooter)

    reloaded_match = Match.load(tmp_path / "match")
    assert reloaded_match.shooters == ["mathias"]
    reloaded_shooter = reloaded_match.load_shooter(tmp_path / "match", "mathias")
    assert reloaded_shooter.selected_shooter_id == 42
    assert reloaded_shooter.stages[1].time_seconds == pytest.approx(8.45)


def test_match_add_shooter_creates_subdir_tree(tmp_path: Path):
    root = tmp_path / "match"
    match = Match.init(root, name="Test")
    match.add_shooter(root, Shooter(slug="x", name="X"))
    for sub in ("raw", "audio", "trimmed", "audit"):
        assert (root / SHOOTERS_DIR / "x" / sub).is_dir(), sub


def test_match_add_shooter_rejects_duplicate_slug(tmp_path: Path):
    root = tmp_path / "match"
    match = Match.init(root, name="Test")
    match.add_shooter(root, Shooter(slug="x", name="X"))
    with pytest.raises(ValueError, match="already registered"):
        match.add_shooter(root, Shooter(slug="x", name="X dup"))


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


def test_is_match_folder(tmp_path: Path):
    root = tmp_path / "match"
    Match.init(root, name="Test")
    assert is_match_folder(root)
    assert not is_legacy_project_folder(root)


def test_is_legacy_project_folder(tmp_path: Path):
    root = tmp_path / "legacy"
    _make_legacy(root, name="Legacy Match", competitor="Anton Johansson")
    assert is_legacy_project_folder(root)
    assert not is_match_folder(root)


def test_from_path_raises_when_neither(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        from_path(empty)


# ---------------------------------------------------------------------------
# Legacy adaptation
# ---------------------------------------------------------------------------


def test_legacy_to_match_view_preserves_core_fields(tmp_path: Path):
    root = tmp_path / "legacy"
    project = _make_legacy(root, name="VADS", competitor="Anton Johansson")

    match, shooter = legacy_to_match_view(project)

    assert match.name == "VADS"
    assert match.scoreboard_match_id == "27242"
    assert match.match_date == date(2026, 4, 3)
    assert match.shooters == ["anton-johansson"]
    assert len(match.stages) == 3
    assert match.stages[0].stage_name == "Egg Grab"

    assert shooter.slug == "anton-johansson"
    assert shooter.name == "Anton Johansson"
    assert shooter.selected_shooter_id == 55429
    assert shooter.stages[0].time_seconds == 11.0


def test_load_match_or_legacy_legacy_path(tmp_path: Path):
    root = tmp_path / "legacy"
    _make_legacy(root, name="VADS", competitor="Anton Johansson")

    match, roots = load_match_or_legacy(root)

    assert match.shooters == ["anton-johansson"]
    assert roots["anton-johansson"] == root


def test_load_match_or_legacy_match_path(tmp_path: Path):
    root = tmp_path / "match"
    match = Match.init(root, name="Test")
    match.add_shooter(root, Shooter(slug="x", name="X"))

    loaded_match, roots = load_match_or_legacy(root)

    assert loaded_match.shooters == ["x"]
    assert roots["x"] == root / SHOOTERS_DIR / "x"


# ---------------------------------------------------------------------------
# Merge planning
# ---------------------------------------------------------------------------


def test_plan_merge_happy_path(tmp_path: Path):
    a = _make_legacy(tmp_path / "anton", name="VADS", competitor="Anton Johansson")
    b = _make_legacy(tmp_path / "martin", name="VADS", competitor="Martin Engström")

    plan = plan_merge([tmp_path / "anton", tmp_path / "martin"], tmp_path / "merged")

    assert plan.name == "VADS"
    assert plan.scoreboard_match_id == "27242"
    assert plan.match_date == date(2026, 4, 3)
    assert len(plan.stages) == 3
    assert [m.slug for m in plan.shooter_moves] == ["anton-johansson", "martin-engstrom"]
    # Suppress unused-var warnings.
    assert a.name == b.name == "VADS"


def test_plan_merge_rejects_scoreboard_mismatch(tmp_path: Path):
    _make_legacy(tmp_path / "a", name="VADS", competitor="Anton", scoreboard_id="111")
    _make_legacy(tmp_path / "b", name="VADS", competitor="Martin", scoreboard_id="222")

    with pytest.raises(MergeConflictError, match="scoreboard_match_id"):
        plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")


def test_plan_merge_rejects_stage_name_disagreement(tmp_path: Path):
    _make_legacy(
        tmp_path / "a",
        name="X",
        competitor="A",
        stage_names=["Egg Grab", "Tower", "Pit"],
    )
    _make_legacy(
        tmp_path / "b",
        name="X",
        competitor="B",
        stage_names=["Egg Grab", "Different Stage 2", "Pit"],
    )

    with pytest.raises(MergeConflictError, match="stage 2"):
        plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")


def test_plan_merge_tolerates_placeholder_loss(tmp_path: Path):
    # One project has a placeholder stage; the other has the real name.
    proj_a = _make_legacy(
        tmp_path / "a", name="X", competitor="A", stage_names=["Stage 1", "S2", "S3"]
    )
    proj_a.stages[0].placeholder = True
    proj_a.save(tmp_path / "a")
    _make_legacy(tmp_path / "b", name="X", competitor="B", stage_names=["Egg Grab", "S2", "S3"])

    plan = plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")
    assert plan.stages[0].stage_name == "Egg Grab"
    assert plan.stages[0].placeholder is False


def test_plan_merge_picks_populated_stage_rounds(tmp_path: Path):
    _make_legacy(
        tmp_path / "a",
        name="X",
        competitor="A",
        stage_rounds_for_first=None,
    )
    _make_legacy(
        tmp_path / "b",
        name="X",
        competitor="B",
        stage_rounds_for_first=StageRounds(expected=12, paper_targets=6, steel_targets=0),
    )

    plan = plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")
    assert plan.stages[0].stage_rounds is not None
    assert plan.stages[0].stage_rounds.expected == 12


def test_plan_merge_requires_name_when_inputs_disagree(tmp_path: Path):
    _make_legacy(tmp_path / "a", name="VADS Easter 2026", competitor="A", scoreboard_id=None)
    _make_legacy(tmp_path / "b", name="vads easter shoot", competitor="B", scoreboard_id=None)

    with pytest.raises(MergeConflictError, match="different names"):
        plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")


def test_plan_merge_accepts_explicit_name_override(tmp_path: Path):
    _make_legacy(tmp_path / "a", name="VADS Easter 2026", competitor="A", scoreboard_id=None)
    _make_legacy(tmp_path / "b", name="vads easter shoot", competitor="B", scoreboard_id=None)

    plan = plan_merge(
        [tmp_path / "a", tmp_path / "b"],
        tmp_path / "merged",
        name="VADS Easter 2026",
    )
    assert plan.name == "VADS Easter 2026"


def test_plan_merge_disambiguates_colliding_slugs(tmp_path: Path):
    _make_legacy(tmp_path / "a", name="X", competitor="Anton Johansson")
    _make_legacy(tmp_path / "b", name="X", competitor="anton johansson")  # same after slugify

    plan = plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")
    assert plan.shooter_moves[0].slug == "anton-johansson"
    assert plan.shooter_moves[1].slug == "anton-johansson-2"


# ---------------------------------------------------------------------------
# Merge execution
# ---------------------------------------------------------------------------


def test_execute_merge_copy_creates_full_layout(tmp_path: Path):
    _make_legacy(tmp_path / "anton", name="VADS", competitor="Anton Johansson")
    _make_legacy(tmp_path / "martin", name="VADS", competitor="Martin Engström")
    # Seed some user data to confirm copy.
    (tmp_path / "anton" / "raw" / "fake.mp4").write_bytes(b"raw-anton")
    (tmp_path / "anton" / "audit" / "stage1.json").write_text('{"shots": []}')
    (tmp_path / "martin" / "raw" / "fake.mp4").write_bytes(b"raw-martin")

    out = tmp_path / "merged"
    plan = plan_merge([tmp_path / "anton", tmp_path / "martin"], out)
    match = execute_merge(plan, move=False)

    # match.json present, shooters subdirs populated. project.json is kept
    # alongside shooter.json as a legacy compat shim (most server endpoints
    # still speak the legacy MatchProject schema); match-level fields are
    # stripped so match.json stays authoritative.
    assert (out / MATCH_FILE).is_file()
    for slug in ("anton-johansson", "martin-engstrom"):
        sroot = out / SHOOTERS_DIR / slug
        assert (sroot / SHOOTER_FILE).is_file(), slug
        assert (sroot / "project.json").is_file(), slug
        assert (sroot / "raw").is_dir(), slug
        legacy = json.loads((sroot / "project.json").read_text())
        assert legacy["scoreboard_match_id"] is None
        assert legacy["scoreboard_content_type"] is None
        assert legacy["match_date"] is None

    # User data carried across.
    raw_file = out / SHOOTERS_DIR / "anton-johansson" / "raw" / "fake.mp4"
    assert raw_file.read_bytes() == b"raw-anton"
    assert (
        out / SHOOTERS_DIR / "anton-johansson" / "audit" / "stage1.json"
    ).read_text() == '{"shots": []}'

    # Sources untouched (copy mode).
    assert (tmp_path / "anton" / "raw" / "fake.mp4").exists()
    assert (tmp_path / "anton" / "project.json").exists()

    # Returned Match is loadable.
    assert match.shooters == ["anton-johansson", "martin-engstrom"]
    Match.load(out)  # raises if invalid


def test_execute_merge_move_relocates_sources(tmp_path: Path):
    _make_legacy(tmp_path / "anton", name="VADS", competitor="Anton")
    _make_legacy(tmp_path / "martin", name="VADS", competitor="Martin")

    out = tmp_path / "merged"
    plan = plan_merge([tmp_path / "anton", tmp_path / "martin"], out)
    execute_merge(plan, move=True)

    assert not (tmp_path / "anton").exists()
    assert not (tmp_path / "martin").exists()
    assert (out / SHOOTERS_DIR / "anton" / SHOOTER_FILE).is_file()


def test_execute_merge_refuses_existing_match(tmp_path: Path):
    _make_legacy(tmp_path / "a", name="X", competitor="A")
    _make_legacy(tmp_path / "b", name="X", competitor="B")
    out = tmp_path / "merged"
    Match.init(out, name="Pre-existing")  # writes match.json

    plan = plan_merge([tmp_path / "a", tmp_path / "b"], out)
    with pytest.raises(FileExistsError):
        execute_merge(plan, move=False)


def test_execute_merge_shooter_json_has_no_match_fields(tmp_path: Path):
    """After merge, shooter.json must not duplicate match-level data.

    The legacy project.json carried name/scoreboard_match_id/match_date as
    top-level fields; in the new schema those live only on match.json.
    """
    _make_legacy(tmp_path / "anton", name="VADS", competitor="Anton")
    out = tmp_path / "merged"
    _make_legacy(tmp_path / "martin", name="VADS", competitor="Martin")

    plan = plan_merge([tmp_path / "anton", tmp_path / "martin"], out)
    execute_merge(plan, move=False)

    shooter_json = json.loads((out / SHOOTERS_DIR / "anton" / SHOOTER_FILE).read_text())
    for forbidden in ("name", "scoreboard_match_id", "scoreboard_content_type", "match_date"):
        # ``name`` IS a Shooter field (the human-readable shooter name), so
        # only the *match*-flavored fields are forbidden. The shooter's own
        # name is fine.
        if forbidden == "name":
            assert shooter_json["name"] == "Anton"
            continue
        assert forbidden not in shooter_json, forbidden
