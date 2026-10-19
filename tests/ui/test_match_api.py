"""Tests for the match-level routes (``ui/match_api.py``)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.automation import AutomationOverride
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app
from tests.ui._helpers import (
    _add_stage_video,
    _build_project_with_primary,
    _connect_fixture_shooter,
    _load_v1_match_fixture,
    _make_match_app,
    _match_create_app,
    _MatchClient,
    _wait_for_job,
)


def test_create_from_scoreboard_rejects_empty_competitor_list(
    tmp_path: Path,
) -> None:
    """Multi-shooter create requires at least one competitor; rejecting
    an empty list early avoids creating an orphan match folder."""
    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/match/create-from-scoreboard",
        json={
            "project_folder": str(tmp_path / "match"),
            "name": "Test Match",
            "match_id": 27190,
            "content_type": 22,
            "competitors": [],
        },
    )
    assert resp.status_code == 400
    assert "at least one competitor" in resp.text


def test_create_from_scoreboard_rejects_duplicate_competitor(
    tmp_path: Path,
) -> None:
    """Same competitor twice in the pick list is a caller bug; 400 with
    a clear message rather than silently de-duping."""
    app = create_app()
    client = _MatchClient(app)
    body = {
        "project_folder": str(tmp_path / "match"),
        "name": "Test Match",
        "match_id": 27190,
        "content_type": 22,
        "competitors": [
            {"name": "Alice", "selected_competitor_id": 7},
            {"name": "Alice (typo)", "selected_competitor_id": 7},
        ],
    }
    resp = client.post("/api/match/create-from-scoreboard", json=body)
    assert resp.status_code == 400
    assert "duplicate" in resp.text
    assert not (tmp_path / "match" / "match.json").exists()


def test_create_from_scoreboard_creates_n_shooters_from_local(
    tmp_path: Path,
) -> None:
    """End-to-end multi-shooter create using LocalJsonScoreboard:
    drop the match fixture in a shared local source (no SsiHttpClient
    needed), pick two competitors, expect two shooters materialised
    with stages populated."""
    import json as _json

    fixture = _load_v1_match_fixture()
    competitors_in_fixture = fixture["competitors"]
    assert len(competitors_in_fixture) >= 2, "fixture must have >= 2 competitors"
    alice = competitors_in_fixture[0]
    bob = competitors_in_fixture[1]

    target = tmp_path / "new-match"
    # Pre-place the match fixture so _resolve_scoreboard_client picks
    # LocalJsonScoreboard for the target instead of trying SsiHttpClient.
    scoreboard_dir = target / "scoreboard"
    scoreboard_dir.mkdir(parents=True)
    (scoreboard_dir / "match.json").write_text(_json.dumps(fixture), encoding="utf-8")

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/match/create-from-scoreboard",
        json={
            "project_folder": str(target),
            "name": fixture["name"],
            "match_id": 27190,
            "content_type": 22,
            "competitors": [
                {
                    "name": alice["name"],
                    "division": alice.get("division"),
                    "selected_shooter_id": alice["shooterId"],
                    "selected_competitor_id": alice["id"],
                },
                {
                    "name": bob["name"],
                    "division": bob.get("division"),
                    "selected_shooter_id": bob["shooterId"],
                    "selected_competitor_id": bob["id"],
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    # Match file written, two shooter dirs under shooters/.
    match_json = _json.loads((target / "match.json").read_text(encoding="utf-8"))
    assert match_json["scoreboard_match_id"] == "27190"
    assert match_json["scoreboard_content_type"] == 22
    assert len(match_json["shooters"]) == 2

    shooter_dirs = sorted((target / "shooters").iterdir())
    assert len(shooter_dirs) == 2
    # Each shooter has a populated project with stages from the match data.
    for shooter_dir in shooter_dirs:
        project = _json.loads((shooter_dir / "project.json").read_text(encoding="utf-8"))
        assert project["selected_competitor_id"] in {alice["id"], bob["id"]}
        assert len(project["stages"]) >= 1

    # Regression: the match itself must carry the stage definitions, not
    # just the per-shooter projects. An empty match.stages would hand a
    # shooter added LATER zero stages (their project is mirrored from it)
    # and make len(match.stages)-based coverage counts read 0.
    assert len(match_json["stages"]) == len(project["stages"])
    assert {s["stage_number"] for s in match_json["stages"]} == {s["stage_number"] for s in project["stages"]}


def test_connect_match_links_and_proposes(tmp_path: Path) -> None:
    import json as _json

    client, match_root, slug, competitor = _connect_fixture_shooter(tmp_path)

    resp = client.post(
        "/api/match/scoreboard/connect",
        json={"match_id": 27190, "content_type": 22},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "proposals" in body and "stage_mismatch" in body
    # The exact-name shooter is proposed against its fixture competitor.
    proposal = next(p for p in body["proposals"] if p["slug"] == slug)
    assert proposal["competitor_id"] == competitor["id"]
    assert proposal["shooter_id"] == competitor["shooterId"]

    # Every shooter's project now carries the match link.
    project = _json.loads((match_root / "shooters" / slug / "project.json").read_text(encoding="utf-8"))
    assert project["scoreboard_match_id"] == "27190"
    assert project["scoreboard_content_type"] == 22


def test_reconcile_applies_links(tmp_path: Path) -> None:
    import json as _json

    client, match_root, slug, competitor = _connect_fixture_shooter(tmp_path)
    connect = client.post(
        "/api/match/scoreboard/connect",
        json={"match_id": 27190, "content_type": 22},
    )
    assert connect.status_code == 200, connect.text

    resp = client.post(
        "/api/match/scoreboard/reconcile",
        json={
            "links": [
                {
                    "slug": slug,
                    "shooter_id": competitor["shooterId"],
                    "competitor_id": competitor["id"],
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text

    project = _json.loads((match_root / "shooters" / slug / "project.json").read_text(encoding="utf-8"))
    assert project["selected_competitor_id"] == competitor["id"]
    assert project["selected_shooter_id"] == competitor["shooterId"]


def test_create_match_manual_scaffolds_match_and_binds_shooter(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """The manual create-match endpoint lays out match.json + the first shooter."""
    from splitsmith import match_model

    target = tmp_path / "new-match"
    app = create_app()
    client = _MatchClient(app)

    body = {
        "name": "Bromma Practice",
        "project_folder": str(target),
        "match_date": "2026-05-14",
        "club": "Bromma PK",
        "default_division": "Production Optics",
        "stages": [
            {"stage_number": 1, "stage_name": "Draw drill", "expected_rounds": 12},
            {"stage_number": 2, "stage_name": "Reload", "expected_rounds": 16},
        ],
        "primary_shooter": {"name": "Mathias Axell", "division": "Production Optics"},
    }
    resp = client.post("/api/match/create-manual", json=body)
    assert resp.status_code == 200, resp.text
    health = resp.json()
    assert health["bound"] is True
    assert health["project_name"] == "Bromma Practice"

    # match.json + shooter dir + nested project.json all exist.
    assert (target / "match.json").exists()
    match = match_model.Match.load(target)
    assert len(match.stages) == 2
    assert match.match_date.isoformat() == "2026-05-14"
    # Slugs are opaque (PII-free): no name leakage on disk or in URLs.
    assert len(match.shooters) == 1
    only_slug = match.shooters[0]
    assert only_slug.startswith("s_") and len(only_slug) == 10

    shooter_root = match_model.Match.shooter_root(target, only_slug)
    assert (shooter_root / "shooter.json").exists()
    assert (shooter_root / "project.json").exists()
    legacy = MatchProject.load(shooter_root)
    assert legacy.competitor_name == "Mathias Axell"
    assert len(legacy.stages) == 2

    # Recent-projects gets only the match folder; the shooter subdir
    # inside it must not register as a standalone legacy project.
    detail = client.get("/api/me/recent-projects?detail=true").json()
    kinds = sorted(p["kind"] for p in detail["projects"])
    assert kinds == ["match"]


def test_create_match_manual_local_mode_requires_project_folder(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """Local mode must reject an omitted project_folder -- the SPA's
    folder picker drives this in local mode and we don't want a missing
    field to silently write somewhere arbitrary (issue #425)."""
    app = create_app()
    client = _MatchClient(app)

    body = {
        "name": "Bromma",
        # project_folder omitted on purpose.
        "stages": [{"stage_number": 1, "stage_name": "One"}],
        "primary_shooter": {"name": "MA"},
    }
    resp = client.post("/api/match/create-manual", json=body)
    assert resp.status_code == 400
    assert "project_folder" in resp.text


def test_create_match_manual_hosted_mode_synthesizes_path(
    tmp_path: Path, monkeypatch, _user_config_home: Path
) -> None:
    """Hosted mode lets the SPA omit ``project_folder``; the server
    synthesizes ``<SPLITSMITH_PROJECTS_DIR>/users/<id>/projects/<slug>/``
    so the create-match flow works without a host filesystem picker
    (issue #425). The SPA discriminates via ``/api/server/features``
    (see ``test_server_features_reports_hosted_mode_when_env_set``)."""
    from splitsmith import match_model
    from splitsmith.ui import server as server_mod

    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(tmp_path / "hosted-root"))

    # Stub the hosted wiring so the test doesn't need a Postgres URL.
    # We still need ``state.auth.user_id`` to be set -- that's the
    # multi-tenant prefix the synthesized path embeds. Use a sentinel
    # value and verify it appears in the resolved match folder.
    from splitsmith.auth import User

    class _StubAuth:
        user_id = "01TESTUSER0000000000000001"
        # A real users row always carries a tier (server default "full");
        # without one the account gate treats the stub as disabled.
        _user = User(id=user_id, email="stub@test", display_name="Stub", access_tier="full")

        async def authenticate_request(self, request):
            return self._user

    def _stub_wiring(state):
        # Stand in for the Postgres wiring: set the stub auth and a tenant
        # factory backed by local in-memory stores, so the per-request auth
        # gate can pin a ``current_tenant`` without a real database. The
        # create-manual flow only needs ``recent_projects`` here (no S3, no
        # matches table), so the rest are minimal.
        state.auth = _StubAuth()
        from splitsmith.ui.jobs import JobRegistry
        from splitsmith.ui.server import TenantContext
        from splitsmith.user_config import (
            JsonRecentProjectsStore,
            JsonScoreboardIdentityStore,
        )

        state._build_tenant = lambda uid: TenantContext(
            user_id=uid,
            recent_projects=JsonRecentProjectsStore(),
            scoreboard_identity=JsonScoreboardIdentityStore(),
            jobs=JobRegistry(),
            matches_store=None,
            project_state=None,
            storage=None,
            share_tokens=None,
            desktop_tokens=None,
            comments=None,
        )

    monkeypatch.setattr(server_mod, "_apply_hosted_mode_wiring", _stub_wiring)

    app = create_app()
    client = _MatchClient(app)

    body = {
        "name": "Bromma Practice",
        # project_folder omitted on purpose -- server picks the path.
        "stages": [{"stage_number": 1, "stage_name": "One"}],
        "primary_shooter": {"name": "MA"},
    }
    resp = client.post("/api/match/create-manual", json=body)
    assert resp.status_code == 200, resp.text

    expected_root = (
        tmp_path / "hosted-root" / "users" / "01TESTUSER0000000000000001" / "projects" / "bromma-practice"
    )
    assert (expected_root / "match.json").exists()
    match = match_model.Match.load(expected_root)
    assert match.name == "Bromma Practice"


def test_create_match_manual_refuses_existing_match_folder(tmp_path: Path, _user_config_home: Path) -> None:
    from splitsmith import match_model

    target = tmp_path / "occupied"
    match_model.Match.init(target, name="Already here")
    app = create_app()
    client = _MatchClient(app)

    body = {
        "name": "Bromma",
        "project_folder": str(target),
        "stages": [{"stage_number": 1, "stage_name": "One"}],
        "primary_shooter": {"name": "MA"},
    }
    resp = client.post("/api/match/create-manual", json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "match_already_exists"


def test_list_match_shooters_returns_active_and_others(tmp_path: Path, _user_config_home: Path) -> None:
    """The shooters endpoint surfaces every registered shooter and flags
    which is currently active. (#324)
    """
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Multi-shooter")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(target)
    for slug, name in [("ma", "Mathias"), ("jl", "Johan")]:
        sh = match_model.Shooter(slug=slug, name=name)
        match.add_shooter(target, sh)
        sroot = match_model.Match.shooter_root(target, slug)
        legacy = MatchProject.init(sroot, name="Multi-shooter")
        legacy.competitor_name = name
        legacy.stages = [
            StageEntry(stage_number=1, stage_name="One", time_seconds=0.0),
            StageEntry(stage_number=2, stage_name="Two", time_seconds=0.0),
        ]
        legacy.save(sroot)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    assert resp.status_code == 200
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    listing = client.get(f"/api/matches/{match_id}/match/shooters")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["match_name"] == "Multi-shooter"
    slugs = [s["slug"] for s in body["shooters"]]
    assert slugs == ["ma", "jl"]


def test_list_match_shooters_includes_per_stage_statuses(tmp_path: Path, _user_config_home: Path) -> None:
    """The shooters payload carries a per-stage status list so the match
    Overview can render an aggregate grid without fetching each project."""
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Multi-shooter")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(target)

    # Shooter "ma": stage 1 has a primary video + time but no audit JSON
    # (-> ready); stage 2 has nothing (-> todo).
    sroot = match_model.Match.shooter_root(target, "ma")
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    legacy = MatchProject.init(sroot, name="Multi-shooter")
    legacy.competitor_name = "Mathias"
    legacy.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[StageVideo(path="s1.mp4", role="primary")],
        ),
        StageEntry(stage_number=2, stage_name="Two", time_seconds=0.0),
    ]
    legacy.save(sroot)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    assert resp.status_code == 200
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    listing = client.get(f"/api/matches/{match_id}/match/shooters")
    assert listing.status_code == 200, listing.text
    ma = next(s for s in listing.json()["shooters"] if s["slug"] == "ma")
    by_stage = {e["stage_number"]: e["status"] for e in ma["stage_statuses"]}
    assert by_stage == {1: "ready", 2: "todo"}


def test_list_match_shooters_counts_unassigned_videos(tmp_path: Path, _user_config_home: Path) -> None:
    """Attached-but-unassigned footage counts toward ``video_count``.

    A hosted upload attached without stage coverage lands in
    ``unassigned_videos`` (disjoint from ``stages[].videos``). Before the
    fix, ``_classify_shooter`` walked only stage videos, so the overview
    reported "NO FOOTAGE YET" for a shooter that already had N videos
    attached. Ignored videos still don't count.
    """
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Multi-shooter")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(target)

    sroot = match_model.Match.shooter_root(target, "ma")
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    legacy = MatchProject.init(sroot, name="Multi-shooter")
    legacy.competitor_name = "Mathias"
    legacy.stages = [
        StageEntry(stage_number=1, stage_name="One", time_seconds=0.0),
        StageEntry(stage_number=2, stage_name="Two", time_seconds=0.0),
    ]
    # Two attached-but-unassigned videos plus one ignored (must not count).
    legacy.unassigned_videos = [
        StageVideo(path="raw/a.mp4", role="secondary"),
        StageVideo(path="raw/b.mp4", role="secondary"),
        StageVideo(path="raw/c.mp4", role="ignored"),
    ]
    legacy.save(sroot)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    assert resp.status_code == 200
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    listing = client.get(f"/api/matches/{match_id}/match/shooters")
    assert listing.status_code == 200, listing.text
    ma = next(s for s in listing.json()["shooters"] if s["slug"] == "ma")
    assert ma["video_count"] == 2


def test_list_match_shooters_stages_total_from_project_not_match(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """stages_total must come from each shooter's own project, not
    ``len(match.stages)``.

    When stages arrive via a per-shooter scoreboard import, the
    match-level stage list can stay empty while the project carries the
    stages. Using ``len(match.stages)`` then rendered a "0 stages" card
    next to a multi-stage match (and broke the audited ratio, whose
    numerator is counted over the project). Regression for that mismatch.
    """
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Empty-match-stages")
    assert match.stages == []  # deliberately empty at the match level
    match.save(target)
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    sroot = match_model.Match.shooter_root(target, "ma")
    legacy = MatchProject.init(sroot, name="Empty-match-stages")
    legacy.competitor_name = "Mathias"
    legacy.stages = [StageEntry(stage_number=n, stage_name=f"Stage {n}", time_seconds=0.0) for n in (1, 2, 3)]
    legacy.save(sroot)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post("/api/me/recent-projects/bind", json={"path": str(target.resolve())})
    assert resp.status_code == 200
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    body = client.get(f"/api/matches/{match_id}/match/shooters").json()
    me = next(s for s in body["shooters"] if s["slug"] == "ma")
    # 3 from legacy.stages -- NOT 0 from the empty match.stages.
    assert me["stages_total"] == 3


def test_add_match_shooter_appends_and_scaffolds(tmp_path: Path, _user_config_home: Path) -> None:
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Add Test")
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    MatchProject.init(match_model.Match.shooter_root(target, "ma"), name="Add Test")

    app = create_app()
    client = _MatchClient(app)
    client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    resp = client.post(f"/api/matches/{match_id}/match/shooters", json={"name": "Johan Larsson"})
    assert resp.status_code == 200
    body = resp.json()
    slugs = [s["slug"] for s in body["shooters"]]
    assert len(slugs) == 2  # original "ma" + the new opaque slug
    new_slug = next(s for s in slugs if s != "ma")
    assert new_slug.startswith("s_") and len(new_slug) == 10
    new_root = match_model.Match.shooter_root(target, new_slug)
    assert (new_root / "project.json").exists()
    assert (new_root / "shooter.json").exists()


def test_add_shooter_with_competitor_pick_sets_ids_and_merges(
    tmp_path: Path, _user_config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/match/shooters with selected_competitor_id binds the new
    shooter to a scoreboard competitor in one step -- no separate
    select-shooter round trip needed.

    Uses the offline LocalJsonScoreboard path (drop the match fixture on
    disk, no SsiHttpClient/network needed). The new shooter's slug is
    minted with a random hex suffix, so the fixture has to be pre-placed
    at a *known* shooter root -- mint_shooter_slug is monkeypatched to a
    fixed value for that reason.
    """
    import json as _json

    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Add Test")
    match.scoreboard_match_id = "27190"
    match.scoreboard_content_type = 22
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    MatchProject.init(match_model.Match.shooter_root(target, "ma"), name="Add Test")

    fixture = _load_v1_match_fixture()
    competitor = fixture["competitors"][0]
    competitor_id = competitor["id"]
    shooter_id = competitor["shooterId"]

    monkeypatch.setattr(match_model, "mint_shooter_slug", lambda taken=None: "s_deadbeef")
    new_root = match_model.Match.shooter_root(target, "s_deadbeef")
    scoreboard_dir = new_root / "scoreboard"
    scoreboard_dir.mkdir(parents=True)
    (scoreboard_dir / "match.json").write_text(_json.dumps(fixture), encoding="utf-8")

    app = create_app()
    client = _MatchClient(app)
    client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    resp = client.post(
        f"/api/matches/{match_id}/match/shooters",
        json={
            "name": competitor["name"],
            "selected_shooter_id": shooter_id,
            "selected_competitor_id": competitor_id,
        },
    )
    assert resp.status_code == 200, resp.text

    project = MatchProject.load(new_root)
    assert project.selected_competitor_id == competitor_id
    assert project.selected_shooter_id == shooter_id
    assert project.competitor_name == competitor["name"]


def test_remove_match_shooter_drops_dir(tmp_path: Path, _user_config_home: Path) -> None:
    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Rm Test")
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    match.add_shooter(target, match_model.Shooter(slug="jl", name="Johan"))
    for slug in ("ma", "jl"):
        MatchProject.init(match_model.Match.shooter_root(target, slug), name="Rm Test")

    app = create_app()
    client = _MatchClient(app)
    client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    resp = client.delete(f"/api/matches/{match_id}/match/shooters/jl")
    assert resp.status_code == 200
    assert not match_model.Match.shooter_root(target, "jl").exists()


def test_merge_plan_returns_reconciled_plan_for_legacy_inputs(tmp_path: Path) -> None:
    """/api/match/merge/plan accepts N legacy single-shooter project
    paths and returns the planned shooter slugs + reconciled stages
    without touching the filesystem."""
    sink = tmp_path / "sink"
    MatchProject.init(sink, name="ignored").save(sink)
    app = _match_create_app(project_root=sink, project_name="ignored")
    client = _MatchClient(app)

    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    MatchProject.init(a, name="Bromma 2026").save(a)
    MatchProject.init(b, name="Bromma 2026").save(b)

    resp = client.post(
        "/api/match/merge/plan",
        json={"inputs": [str(a), str(b)]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Bromma 2026"
    assert len(body["shooter_moves"]) == 2
    slugs = {mv["slug"] for mv in body["shooter_moves"]}
    assert all(s.startswith("s_") and len(s) == 10 for s in slugs), slugs
    # Plan must not have written anything to disk.
    assert not (tmp_path / "merged").exists()


def test_merge_plan_returns_409_on_conflicting_names(tmp_path: Path) -> None:
    """When inputs disagree on MatchProject.name and the caller omits an
    explicit --name, /api/match/merge/plan returns 409 with the
    MergeConflictError message so the SPA can surface it."""
    sink = tmp_path / "sink"
    MatchProject.init(sink, name="ignored").save(sink)
    app = _match_create_app(project_root=sink, project_name="ignored")
    client = _MatchClient(app)

    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    MatchProject.init(a, name="Bromma 2026").save(a)
    MatchProject.init(b, name="Vasteras 2026").save(b)

    resp = client.post(
        "/api/match/merge/plan",
        json={"inputs": [str(a), str(b)]},
    )
    assert resp.status_code == 409
    assert "different names" in resp.json()["detail"]


def test_merge_execute_writes_match_folder_and_binds_first_shooter(tmp_path: Path) -> None:
    """/api/match/merge/execute writes the new match folder, registers
    it in recent-projects, and binds the first shooter so the SPA can
    navigate to / and resume work immediately."""
    sink = tmp_path / "sink"
    MatchProject.init(sink, name="ignored").save(sink)
    app = _match_create_app(project_root=sink, project_name="ignored")
    client = _MatchClient(app)

    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    MatchProject.init(a, name="Match X").save(a)
    MatchProject.init(b, name="Match X").save(b)

    out = tmp_path / "merged"
    resp = client.post(
        "/api/match/merge/execute",
        json={"inputs": [str(a), str(b)], "output": str(out), "name": "Match X"},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["bound"] is True
    # Bound shooter root sits under the merged match's shooters dir.
    assert str(out) in body["project_root"]
    assert (out / "match.json").exists()
    # Two shooter subdirs, named with opaque ids (no PII on disk).
    shooter_names = sorted(p.name for p in (out / "shooters").iterdir())
    assert len(shooter_names) == 2
    assert all(n.startswith("s_") and len(n) == 10 for n in shooter_names), shooter_names


def test_merge_execute_refuses_destination_with_existing_match(tmp_path: Path) -> None:
    """/api/match/merge/execute returns 409 when the destination already
    contains a match.json so a stray click can't clobber an existing
    merged match."""
    sink = tmp_path / "sink"
    MatchProject.init(sink, name="ignored").save(sink)
    app = _match_create_app(project_root=sink, project_name="ignored")
    client = _MatchClient(app)

    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    MatchProject.init(a, name="Match X").save(a)
    MatchProject.init(b, name="Match X").save(b)

    out = tmp_path / "merged"
    out.mkdir()
    (out / "match.json").write_text("{}")

    resp = client.post(
        "/api/match/merge/execute",
        json={"inputs": [str(a), str(b)], "output": str(out), "name": "Match X"},
    )
    assert resp.status_code == 409


def test_match_id_alias_rewrites_to_existing_route(tmp_path: Path, _user_config_home: Path) -> None:
    """``/api/matches/{match_id}/health`` resolves to the same health
    route. Health is identical on both prefixes -- after Tier 1
    step 4 of doc 10 it doesn't echo back the bound state or the
    match id; it's a process-level "we're up" check.
    """
    app, match, _ = _make_match_app(tmp_path)
    client = _MatchClient(app)

    direct = client.get("/api/health")
    aliased = client.get(f"/api/matches/{match.match_id}/health")

    assert direct.status_code == 200
    assert aliased.status_code == 200
    assert direct.json() == aliased.json()
    assert direct.json()["bound"] is False
    assert direct.json()["match_id"] is None


def test_match_id_alias_resolves_match_level_endpoint(tmp_path: Path, _user_config_home: Path) -> None:
    """A real match-level endpoint works through the /api/matches/
    prefix; the bare-path form 409s after Tier 1 of the singleton-
    elimination work (doc 10) -- match-level operations are
    addressable only by URL, never via a process-level bind.

    Uses a raw ``TestClient`` (not the ``_MatchClient`` wrapper) so
    the bare-path call actually reaches the server bare; the wrapper
    would auto-prefix and defeat the point of the test.
    """
    app, match, _ = _make_match_app(tmp_path)
    client = TestClient(app)

    aliased = client.get(f"/api/matches/{match.match_id}/match/shooters")
    bare = client.get("/api/match/shooters")

    assert aliased.status_code == 200
    assert bare.status_code == 409
    assert bare.json()["detail"]["code"] == "no_project"


def test_match_id_alias_unknown_id_returns_404(tmp_path: Path, _user_config_home: Path) -> None:
    app, _, _ = _make_match_app(tmp_path)
    client = _MatchClient(app)

    resp = client.get("/api/matches/nope-deadbeef/health")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "match_not_found"


def test_match_id_alias_serves_non_bound_match(tmp_path: Path, _user_config_home: Path) -> None:
    """A non-bound match resolves independently of the bound singleton (#353 Phase 3 PR C).

    PR B briefly enforced ``match_id == state.matches.known_ids()``; PR C
    drops that check because the middleware sets a per-request
    ContextVar -- two tabs on different matches each get their own
    scope. Both match ids must be served when both are registered.
    """
    from splitsmith import match_model, user_config

    app, bound_match, _ = _make_match_app(tmp_path)
    other_root = tmp_path / "other-match"
    other = match_model.Match.init(other_root, name="Other Match")
    user_config.record_project_open(other_root, other.name, kind="match")

    client = _MatchClient(app)
    # The bound match still resolves via its own id.
    resp = client.get(f"/api/matches/{bound_match.match_id}/health")
    assert resp.status_code == 200
    # The non-bound match also resolves; no more 409 ``match_mismatch``.
    resp = client.get(f"/api/matches/{other.match_id}/health")
    assert resp.status_code == 200


def test_match_id_alias_missing_segment_returns_400(tmp_path: Path, _user_config_home: Path) -> None:
    app, _, _ = _make_match_app(tmp_path)
    client = _MatchClient(app)

    # No trailing segment after /api/matches/{id} -- the middleware can't
    # rewrite to a real route without one.
    resp = client.get("/api/matches/")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "match_id_required"


def test_beep_queue_proxy_ready_local_mode_always_true(tmp_path: Path) -> None:
    """In local mode (state.storage is None) every beep-queue item carries
    proxy_ready=True - source streams directly from disk so there is no
    "generating" state to report.

    The proxy_ready contract: _storage is None -> True for all paths,
    regardless of whether a raw_proxy/ object exists.
    """
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "Beep Queue Proxy Ready",
        beep_time=10.0,
        beep_source="auto",  # type: ignore[arg-type]
        beep_confidence=0.4,
        beep_reviewed=False,
    )
    # _build_project_with_primary stores the path as an absolute path
    # (root / "primary.mp4"). The _proxy_ready helper returns True for
    # paths not starting with "raw/" - both that rule and the "storage is
    # None" rule guarantee True in local mode.
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/match/beep-queue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = [item for stage in body["stages"] for item in stage["items"]]
    assert len(items) == 1, f"expected 1 beep-queue item, got {len(items)}"
    assert items[0]["proxy_ready"] is True, "local mode must always report proxy_ready=True"


def test_beep_queue_includes_secondary_videos(tmp_path: Path) -> None:
    """A secondary with an unreviewed auto beep is a first-class queue item.

    Before this behavior existed the queue enumerated only primaries, so a
    wrong secondary auto-beep (echo, neighbouring timer) had no review
    surface anywhere in the SPA - Audit's sync pill deep-linked into the
    queue with a focus key that matched nothing.
    """
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "Secondary In Queue",
        beep_time=25.13,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    secondary_id = _add_stage_video(
        root,
        role="secondary",
        filename="secondary.mp4",
        beep_time=5.95,
        beep_source="auto",
        # Above the global default threshold (0.95, see
        # test_get_automation_returns_resolved_settings_and_provenance) so
        # this exercises the "unreviewed" status specifically, not
        # "low_confidence" - #823 fixed get_beep_queue's threshold
        # resolution, which used to silently stick at a 0.5 fallback.
        beep_confidence=0.98,
        beep_reviewed=False,
    )
    # Ignored videos are skipped entirely by the pipeline and must not
    # surface in the queue.
    _add_stage_video(
        root,
        role="ignored",
        filename="ignored.mp4",
        beep_time=None,
        beep_source=None,
        beep_confidence=None,
        beep_reviewed=False,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/match/beep-queue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = [item for stage in body["stages"] for item in stage["items"]]
    assert [it["video_id"] for it in items] == [secondary_id]
    assert items[0]["role"] == "secondary"
    assert items[0]["status"] == "unreviewed"
    # Reviewed primary counts as confirmed; the ignored video is invisible.
    assert body["total_items"] == 2
    assert body["pending_count"] == 1
    assert body["confirmed_count"] == 1
    group = body["stages"][0]
    assert group["total_videos"] == 2
    assert group["confirmed"] == 1

    # include_confirmed=true surfaces both, primary first.
    resp = client.get("/api/match/beep-queue?include_confirmed=true")
    items = [item for stage in resp.json()["stages"] for item in stage["items"]]
    assert [it["role"] for it in items] == ["primary", "secondary"]
    assert items[0]["status"] == "confirmed"


def test_beep_queue_confirm_secondary(tmp_path: Path) -> None:
    """Confirming a secondary's beep through the queue flips beep_reviewed
    on the secondary video and leaves the primary untouched."""
    from splitsmith import match_model

    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "Confirm Secondary",
        beep_time=25.13,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    secondary_id = _add_stage_video(
        root,
        role="secondary",
        filename="secondary.mp4",
        beep_time=5.95,
        beep_source="auto",
        beep_confidence=0.8,
        beep_reviewed=False,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/match/beep-queue/confirm",
        json={"slug": "me", "stage_number": 1, "video_id": secondary_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending_count"] == 0

    shooter_root = match_model.Match.shooter_root(root, "me")
    project = MatchProject.load(shooter_root)
    videos = project.stages[0].videos
    secondary = next(v for v in videos if v.video_id == secondary_id)
    assert secondary.beep_reviewed is True
    assert secondary.beep_time == 5.95


def test_beep_queue_confirm_primary_chains_shot_detect(tmp_path: Path) -> None:
    """Confirming a low-confidence primary through the queue must submit
    shot detection when its trim is already cached -- the same chain the
    per-video beep-reviewed endpoint fires. Before this fix the queue
    only flipped the flag, leaving a trimmed stage with no shots."""
    root = tmp_path / "match"
    project = _build_project_with_primary(
        root,
        "Confirm Primary",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.4,
        beep_reviewed=False,
    )
    project.stages[0].videos[0].processed["trim"] = True
    project.save(root / "shooters" / "me")
    # video_id hashes "<path>#<stage_number>" and stage_number is stamped
    # on load - read the id back from a fresh load, not the local object.
    primary_id = MatchProject.load(root / "shooters" / "me").stages[0].videos[0].video_id
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)
    state = app.state.splitsmith_state

    release = threading.Event()

    def _blocked(_handle, **_args) -> None:
        release.wait(timeout=10.0)

    state.jobs.bodies.register("shot_detect", _blocked)
    try:
        resp = client.post(
            "/api/match/beep-queue/confirm",
            json={"slug": "me", "stage_number": 1, "video_id": primary_id},
        )
        assert resp.status_code == 200, resp.text
        jobs = client.get("/api/me/jobs").json()
        kinds = [(j["kind"], j["shooter_slug"], j["stage_number"]) for j in jobs]
        assert ("shot_detect", "me", 1) in kinds, kinds
    finally:
        release.set()
    for j in client.get("/api/me/jobs").json():
        if j["kind"] == "shot_detect":
            _wait_for_job(client, j["id"])


def test_beep_queue_confirm_primary_submits_trim_when_uncached(tmp_path: Path) -> None:
    """Confirming the detector's beep as-is on an untrimmed stage must
    submit the trim (which chains detection once it lands). Before this
    fix only a manual override chained a trim; a plain confirm left the
    stage untrimmed with no shots until someone pressed Run trim (spec
    2026-09-13 s4.4, UX PR 5)."""
    root = tmp_path / "match"
    project = _build_project_with_primary(
        root,
        "Confirm Untrimmed",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.4,
        beep_reviewed=False,
    )
    assert project.stages[0].videos[0].processed.get("trim") is not True
    assert project.stages[0].time_seconds > 0
    project.save(root / "shooters" / "me")
    primary_id = MatchProject.load(root / "shooters" / "me").stages[0].videos[0].video_id
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)
    state = app.state.splitsmith_state

    release = threading.Event()

    def _blocked(_handle, **_args) -> None:
        release.wait(timeout=10.0)

    state.jobs.bodies.register("trim", _blocked)
    try:
        resp = client.post(
            "/api/match/beep-queue/confirm",
            json={"slug": "me", "stage_number": 1, "video_id": primary_id},
        )
        assert resp.status_code == 200, resp.text
        jobs = client.get("/api/me/jobs").json()
        kinds = [(j["kind"], j["shooter_slug"], j["stage_number"]) for j in jobs]
        assert ("trim", "me", 1) in kinds, kinds
    finally:
        release.set()
    for j in client.get("/api/me/jobs").json():
        if j["kind"] == "trim":
            _wait_for_job(client, j["id"])


def test_beep_queue_threshold_respects_project_override(tmp_path: Path) -> None:
    """Project-level threshold override flows through to item status.

    Regression test for #823: ``get_beep_queue`` called
    ``resolve_automation`` positionally, which raises ``TypeError``
    against the keyword-only signature and gets swallowed by the
    surrounding ``except Exception: continue`` - the threshold silently
    stuck at the 0.5 fallback instead of reflecting the override. At
    confidence=0.65 that bug classifies the item as ``unreviewed``
    (0.65 > 0.5); with the override correctly resolved to 0.9, the same
    item is ``low_confidence`` (0.65 < 0.9).
    """
    root = tmp_path / "match"
    project = _build_project_with_primary(
        root,
        "Beep Queue Threshold",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.65,
        beep_reviewed=False,
    )
    project.automation = AutomationOverride(beep_low_confidence_threshold=0.9)
    project.save(root / "shooters" / "me")
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/match/beep-queue")
    assert resp.status_code == 200, resp.text
    items = [item for stage in resp.json()["stages"] for item in stage["items"]]
    assert len(items) == 1
    assert items[0]["status"] == "low_confidence"


def test_the_queue_confirms_a_secondary_beep_before_its_clip(tmp_path: Path) -> None:
    from splitsmith import match_model

    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "Before the clip",
        beep_time=25.13,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    secondary_id = _add_stage_video(
        root,
        role="secondary",
        filename="secondary.mp4",
        beep_time=0.0,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/match/beep-queue/confirm",
        json={"slug": "me", "stage_number": 1, "video_id": secondary_id, "time": -0.21, "source": "manual"},
    )
    assert resp.status_code == 200, resp.text
    project = MatchProject.load(match_model.Match.shooter_root(root, "me"))
    secondary = next(v for v in project.stages[0].videos if v.video_id == secondary_id)
    assert secondary.beep_time == pytest.approx(-0.21)

    primary_id = next(v.video_id for v in project.stages[0].videos if v.role == "primary")
    refused = client.post(
        "/api/match/beep-queue/confirm",
        json={"slug": "me", "stage_number": 1, "video_id": primary_id, "time": -0.21, "source": "manual"},
    )
    assert refused.status_code == 400
