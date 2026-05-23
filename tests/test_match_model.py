"""Tests for the match-as-object data model (issue #320)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from splitsmith.config import StageRounds
from splitsmith.match_model import (
    MATCH_FILE,
    MATCH_SCHEMA_VERSION,
    SHOOTER_FILE,
    SHOOTERS_DIR,
    Match,
    MatchStageDefinition,
    MergeConflictError,
    Shooter,
    ShooterStageData,
    execute_merge,
    from_path,
    generate_match_id,
    is_legacy_project_folder,
    is_match_folder,
    legacy_to_match_view,
    load_match_or_legacy,
    mint_shooter_slug,
    plan_merge,
    slugify_filename,
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


def test_mint_shooter_slug_shape():
    slug = mint_shooter_slug()
    assert slug.startswith("s_")
    assert len(slug) == 10  # "s_" + 8 hex chars
    assert all(c in "0123456789abcdef" for c in slug[2:])


def test_mint_shooter_slug_avoids_taken():
    """The minter retries until it gets a slug outside ``taken``."""
    slug = mint_shooter_slug(taken={"s_00000000"})
    assert slug != "s_00000000"


def test_mint_shooter_slug_uniqueness():
    """Two calls without coordination still collide vanishingly rarely; the
    primary safety is the ``taken`` set, which the helper consults."""
    slugs = {mint_shooter_slug() for _ in range(100)}
    assert len(slugs) == 100


def test_slugify_filename_basic():
    assert slugify_filename("Stage 1: H1") == "stage-1-h1"


def test_slugify_filename_handles_accents():
    assert slugify_filename("Långvägen") == "langvagen"


def test_slugify_filename_empty_falls_back():
    assert slugify_filename("") == "name"
    assert slugify_filename("---") == "name"


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
    assert len(match.shooters) == 1
    only_slug = match.shooters[0]
    assert only_slug.startswith("s_")
    assert len(match.stages) == 3
    assert match.stages[0].stage_name == "Egg Grab"

    assert shooter.slug == only_slug
    assert shooter.name == "Anton Johansson"
    assert shooter.selected_shooter_id == 55429
    assert shooter.stages[0].time_seconds == 11.0


def test_legacy_to_match_view_slug_is_deterministic(tmp_path: Path):
    """Same project metadata -> same opaque slug across reloads."""
    root = tmp_path / "legacy"
    project = _make_legacy(root, name="VADS", competitor="Anton Johansson")

    match_a, _ = legacy_to_match_view(project)
    match_b, _ = legacy_to_match_view(project)
    assert match_a.shooters == match_b.shooters


def test_load_match_or_legacy_legacy_path(tmp_path: Path):
    root = tmp_path / "legacy"
    _make_legacy(root, name="VADS", competitor="Anton Johansson")

    match, roots = load_match_or_legacy(root)

    assert len(match.shooters) == 1
    only_slug = match.shooters[0]
    assert only_slug.startswith("s_")
    assert roots[only_slug] == root


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
    slugs = [m.slug for m in plan.shooter_moves]
    assert all(s.startswith("s_") for s in slugs), slugs
    assert len(set(slugs)) == 2
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
    proj_a = _make_legacy(tmp_path / "a", name="X", competitor="A", stage_names=["Stage 1", "S2", "S3"])
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


def test_plan_merge_assigns_opaque_slugs(tmp_path: Path):
    """Slugs are random opaque ids (``s_<hex>``), not derived from the
    competitor names, so the on-disk layout doesn't leak PII."""
    _make_legacy(tmp_path / "a", name="X", competitor="Anton Johansson")
    _make_legacy(tmp_path / "b", name="X", competitor="Martin Engström")

    plan = plan_merge([tmp_path / "a", tmp_path / "b"], tmp_path / "merged")
    slugs = [m.slug for m in plan.shooter_moves]
    assert all(s.startswith("s_") and len(s) == 10 for s in slugs), slugs
    assert len(set(slugs)) == len(slugs), "slugs must be unique"


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

    # Resolve the opaque slug for each source so the asserts below don't
    # need to know the random ids.
    moves_by_src = {m.source_root: m for m in plan.shooter_moves}
    anton_slug = moves_by_src[tmp_path / "anton"].slug
    martin_slug = moves_by_src[tmp_path / "martin"].slug

    # match.json present, shooters subdirs populated. project.json is kept
    # alongside shooter.json as a legacy compat shim (most server endpoints
    # still speak the legacy MatchProject schema); match-level fields are
    # stripped so match.json stays authoritative.
    assert (out / MATCH_FILE).is_file()
    for slug in (anton_slug, martin_slug):
        sroot = out / SHOOTERS_DIR / slug
        assert (sroot / SHOOTER_FILE).is_file(), slug
        assert (sroot / "project.json").is_file(), slug
        assert (sroot / "raw").is_dir(), slug
        legacy = json.loads((sroot / "project.json").read_text())
        assert legacy["scoreboard_match_id"] is None
        assert legacy["scoreboard_content_type"] is None
        assert legacy["match_date"] is None

    # User data carried across.
    raw_file = out / SHOOTERS_DIR / anton_slug / "raw" / "fake.mp4"
    assert raw_file.read_bytes() == b"raw-anton"
    assert (out / SHOOTERS_DIR / anton_slug / "audit" / "stage1.json").read_text() == '{"shots": []}'

    # Sources untouched (copy mode).
    assert (tmp_path / "anton" / "raw" / "fake.mp4").exists()
    assert (tmp_path / "anton" / "project.json").exists()

    # Returned Match is loadable.
    assert sorted(match.shooters) == sorted([anton_slug, martin_slug])
    Match.load(out)  # raises if invalid


def test_execute_merge_move_relocates_sources(tmp_path: Path):
    _make_legacy(tmp_path / "anton", name="VADS", competitor="Anton")
    _make_legacy(tmp_path / "martin", name="VADS", competitor="Martin")

    out = tmp_path / "merged"
    plan = plan_merge([tmp_path / "anton", tmp_path / "martin"], out)
    execute_merge(plan, move=True)

    assert not (tmp_path / "anton").exists()
    assert not (tmp_path / "martin").exists()
    match = Match.load(out)
    assert len(match.shooters) == 2
    for slug in match.shooters:
        assert (out / SHOOTERS_DIR / slug / SHOOTER_FILE).is_file()


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

    anton_slug = next(m.slug for m in plan.shooter_moves if m.source_root == tmp_path / "anton")
    shooter_json = json.loads((out / SHOOTERS_DIR / anton_slug / SHOOTER_FILE).read_text())
    for forbidden in ("name", "scoreboard_match_id", "scoreboard_content_type", "match_date"):
        # ``name`` IS a Shooter field (the human-readable shooter name), so
        # only the *match*-flavored fields are forbidden. The shooter's own
        # name is fine.
        if forbidden == "name":
            assert shooter_json["name"] == "Anton"
            continue
        assert forbidden not in shooter_json, forbidden


# ---------------------------------------------------------------------------
# match_id (issue #353 Phase 3 PR A)
# ---------------------------------------------------------------------------


def test_generate_match_id_is_deterministic_for_same_inputs():
    from datetime import UTC, datetime

    ts = datetime(2026, 4, 3, 12, 0, 0, tzinfo=UTC)
    a = generate_match_id("VADS Easter Shoot 2026", ts)
    b = generate_match_id("VADS Easter Shoot 2026", ts)
    assert a == b
    assert a.startswith("vads-easter-shoot-2026-")
    # ``<slug>-<10-char hex>``
    assert len(a.rsplit("-", 1)[1]) == 10


def test_generate_match_id_differs_for_distinct_timestamps():
    from datetime import UTC, datetime

    a = generate_match_id("VADS", datetime(2026, 1, 1, tzinfo=UTC))
    b = generate_match_id("VADS", datetime(2026, 1, 2, tzinfo=UTC))
    assert a != b
    # Same slug prefix, different hash tail.
    assert a.split("-")[0] == b.split("-")[0] == "vads"


def test_generate_match_id_handles_unicode_and_punctuation():
    from datetime import UTC, datetime

    mid = generate_match_id("Brömma Klassikern 2026!", datetime(2026, 1, 1, tzinfo=UTC))
    assert mid.startswith("bromma-klassikern-2026-")


def test_generate_match_id_empty_name_falls_back():
    from datetime import UTC, datetime

    mid = generate_match_id("!!!", datetime(2026, 1, 1, tzinfo=UTC))
    assert mid.startswith("match-")


def test_match_init_assigns_match_id(tmp_path: Path):
    match = Match.init(tmp_path / "match", name="VADS Easter Shoot 2026")
    assert match.match_id is not None
    assert match.match_id.startswith("vads-easter-shoot-2026-")


def test_match_id_persists_across_reload(tmp_path: Path):
    match = Match.init(tmp_path / "match", name="Persisted")
    minted = match.match_id
    reloaded = Match.load(tmp_path / "match")
    assert reloaded.match_id == minted
    # Frozen on disk too.
    on_disk = json.loads((tmp_path / "match" / MATCH_FILE).read_text())
    assert on_disk["match_id"] == minted
    assert on_disk["schema_version"] == MATCH_SCHEMA_VERSION


def test_load_migrates_pre_v4_match_in_place(tmp_path: Path):
    """A match.json written before #353 (no match_id) gets one on first load."""
    root = tmp_path / "legacy-match"
    root.mkdir()
    # Hand-write a v3-shaped match.json (no match_id; older schema_version).
    legacy_payload = {
        "schema_version": 3,
        "name": "Legacy",
        "match_id": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "shooters": [],
        "stages": [],
    }
    (root / MATCH_FILE).write_text(json.dumps(legacy_payload))

    loaded = Match.load(root)
    assert loaded.match_id is not None
    # Persisted: a second read sees the same id without another migration.
    second = Match.load(root)
    assert second.match_id == loaded.match_id
    # The on-disk file now has it too.
    on_disk = json.loads((root / MATCH_FILE).read_text())
    assert on_disk["match_id"] == loaded.match_id


def test_rename_does_not_change_match_id(tmp_path: Path):
    """Once minted, match_id is frozen; renaming the match is purely cosmetic."""
    match = Match.init(tmp_path / "match", name="Original Name")
    original_id = match.match_id
    match.name = "Renamed Match"
    match.save(tmp_path / "match")
    reloaded = Match.load(tmp_path / "match")
    assert reloaded.match_id == original_id
    assert reloaded.name == "Renamed Match"
