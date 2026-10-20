"""Tests for the account-level routes (``ui/me_api.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app
from tests.ui._helpers import (
    _match_create_app,
    _MatchClient,
    _seed_project_with_primary,
)


def test_cancel_endpoint_returns_404_for_unknown_job(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/me/jobs/does-not-exist/cancel")
    assert resp.status_code == 404


def test_retry_endpoint_returns_404_for_unknown_job(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/me/jobs/does-not-exist/retry")
    assert resp.status_code == 404


def test_get_job_404_when_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/me/jobs/does-not-exist")
    assert resp.status_code == 404


def test_acknowledge_endpoint_404_for_unknown_job(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/me/jobs/does-not-exist/acknowledge")
    assert resp.status_code == 404


def test_recent_projects_endpoint(tmp_path: Path, _user_config_home: Path) -> None:
    _match_create_app(project_root=tmp_path / "alpha", project_name="Alpha")
    app = _match_create_app(project_root=tmp_path / "beta", project_name="Beta")
    client = _MatchClient(app)

    resp = client.get("/api/me/recent-projects")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["projects"]]
    assert names[0] == "Beta"  # most-recent first
    assert "Alpha" in names


def test_delete_recent_project_endpoint(tmp_path: Path, _user_config_home: Path) -> None:
    _match_create_app(project_root=tmp_path / "alpha", project_name="Alpha")
    app = _match_create_app(project_root=tmp_path / "beta", project_name="Beta")
    client = _MatchClient(app)

    alpha_root = tmp_path / "alpha"
    resp = client.post(
        "/api/me/recent-projects/delete",
        json={"path": str(alpha_root.resolve())},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["recent_project_removed"] is True
    assert [p["name"] for p in body["projects"]] == ["Beta"]
    # Default delete_local_files=False -> the folder stays on disk; only the
    # picker entry is dropped.
    assert body["summary"]["local_dir_removed"] is False
    assert alpha_root.exists()


def test_delete_recent_project_optin_removes_local_dir(tmp_path: Path, _user_config_home: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "gamma", project_name="Gamma")
    client = _MatchClient(app)

    gamma_root = tmp_path / "gamma"
    assert gamma_root.exists()
    resp = client.post(
        "/api/me/recent-projects/delete",
        json={"path": str(gamma_root.resolve()), "delete_local_files": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["local_dir_removed"] is True
    assert not gamma_root.exists()


def test_bind_recent_project_404_when_path_missing(tmp_path: Path, _user_config_home: Path) -> None:
    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(tmp_path / "does-not-exist")},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "project_path_missing"


def test_bind_recent_project_refuses_non_match_path(tmp_path: Path, _user_config_home: Path) -> None:
    """Post Tier 1 step 3 of doc 10: bind endpoint refuses any path
    that isn't already a Match folder. Picker UX redirects to the
    explicit create-match endpoints instead of inline scaffolding.
    """
    app = create_app()
    client = TestClient(app)
    target = tmp_path / "empty-folder"
    target.mkdir()

    resp = client.post("/api/me/recent-projects/bind", json={"path": str(target)})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_a_match"


def test_scoreboard_identity_round_trip(tmp_path: Path, _user_config_home: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    # Empty: 200 null. "Not pinned yet" is a normal state, not an error,
    # so the SPA doesn't have to catch a 404 on every page load.
    empty = client.get("/api/me/scoreboard-identity")
    assert empty.status_code == 200
    assert empty.json() is None

    payload = {
        "shooter_id": 12345,
        "display_name": "Mathias Axell",
        "division": "Production Optics",
        "club": "Bromma PK",
        "base_url": "https://shootnscoreit.com",
    }
    resp = client.put("/api/me/scoreboard-identity", json=payload)
    assert resp.status_code == 200
    assert resp.json()["shooter_id"] == 12345

    # Now readable.
    got = client.get("/api/me/scoreboard-identity")
    assert got.status_code == 200
    assert got.json()["display_name"] == "Mathias Axell"

    # Delete clears it back to null.
    client.delete("/api/me/scoreboard-identity")
    cleared = client.get("/api/me/scoreboard-identity")
    assert cleared.status_code == 200
    assert cleared.json() is None


def test_recent_projects_detail_enriches_metadata(tmp_path: Path, _user_config_home: Path) -> None:
    """`?detail=true` returns kind + stage/shooter counts for the redesign picker (#322)."""
    from splitsmith import match_model, user_config

    # Post Tier 1 step 3 of doc 10, only Match folders are
    # enumerable as a real project kind in the picker. A pre-Tier-1
    # legacy ``project.json`` shows up as ``kind="unknown"`` so
    # the user can prune it; the metadata block stays empty.
    match_root = tmp_path / "fancy-match"
    match = match_model.Match.init(match_root, name="Fancy Match")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(match_root)
    shooter = match_model.Shooter(slug="ma", name="Mathias")
    match.add_shooter(match_root, shooter)
    MatchProject.init(match_model.Match.shooter_root(match_root, "ma"), name="Fancy Match")
    user_config.record_project_open(match_root, match.name, kind="match")

    app = create_app()
    client = _MatchClient(app)

    resp = client.get("/api/me/recent-projects?detail=true")
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    by_kind = {p["kind"]: p for p in projects}

    assert by_kind["match"]["name"] == "Fancy Match"
    assert by_kind["match"]["shooter_count"] == 1
    assert by_kind["match"]["stage_count"] == 2
    # No footage attached -> "awaiting_footage". The picker uses this
    # to flip from the default red "needs your attention" treatment to
    # a softer "ready for footage" empty state (#425).
    assert by_kind["match"]["status"] == "awaiting_footage"
    assert by_kind["match"]["video_count"] == 0
    assert by_kind["match"]["next_step"] == {
        "kind": "footage",
        "shooter_slug": None,
        "stage_number": None,
        "stage_name": None,
    }


def test_recent_projects_detail_reports_whether_a_match_was_synced(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """The Matches row's delete confirm says whether a hosted copy exists,
    which a desktop delete does not touch; ``synced`` is what it reads."""
    from datetime import UTC, datetime

    from splitsmith import match_model, user_config
    from splitsmith.sync.state import SyncState, save_sync_state

    roots = {}
    for name in ("Synced Match", "Local Match"):
        root = tmp_path / name.lower().replace(" ", "-")
        match = match_model.Match.init(root, name=name)
        match.save(root)
        user_config.record_project_open(root, name, kind="match")
        roots[name] = root
    save_sync_state(roots["Synced Match"], SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC)))

    client = _MatchClient(create_app())
    by_name = {p["name"]: p for p in client.get("/api/me/recent-projects?detail=true").json()["projects"]}
    assert by_name["Synced Match"]["synced"] is True
    assert by_name["Local Match"]["synced"] is False


def test_recent_projects_detail_in_progress_once_footage_attached(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """Once any video lands on the project the status flips from
    ``awaiting_footage`` to ``in_progress`` (the existing in-flight
    state). Drives the MatchShell menu treatment per #425."""
    from splitsmith import match_model, user_config

    match_root = tmp_path / "with-footage"
    match = match_model.Match.init(match_root, name="With Footage")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="One")]
    match.save(match_root)
    shooter = match_model.Shooter(slug="ma", name="Mathias")
    match.add_shooter(match_root, shooter)
    shooter_root = match_model.Match.shooter_root(match_root, "ma")
    legacy = MatchProject.init(shooter_root, name="With Footage")
    # Drop a placeholder video into the registry. ``StageEntry`` accepts
    # a videos list; a single entry is enough to flip the status.
    legacy.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=0.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary")],
        )
    ]
    legacy.save(shooter_root)
    user_config.record_project_open(match_root, match.name, kind="match")

    app = create_app()
    client = _MatchClient(app)
    resp = client.get("/api/me/recent-projects?detail=true")
    assert resp.status_code == 200
    by_kind = {p["kind"]: p for p in resp.json()["projects"]}
    assert by_kind["match"]["status"] == "in_progress"
    assert by_kind["match"]["video_count"] == 1


def test_recent_projects_detail_names_the_next_step(tmp_path: Path, _user_config_home: Path) -> None:
    """The picker's Continue card names the first open stage (UX PR 8).

    Stage 1 is audited (a ``save`` event in its audit doc), stage 2 has a
    primary and a time but no audit yet, so ``next_step`` is an audit of
    stage 2 for this shooter. The no-footage fixture reports ``footage``.
    """
    import json as _json

    from splitsmith import match_model, user_config

    match_root = tmp_path / "next-step"
    match = match_model.Match.init(match_root, name="Next Step")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(match_root)
    match.add_shooter(match_root, match_model.Shooter(slug="ma", name="Mathias"))
    shooter_root = match_model.Match.shooter_root(match_root, "ma")
    legacy = MatchProject.init(shooter_root, name="Next Step")
    legacy.stages = [
        StageEntry(
            stage_number=n,
            stage_name=name,
            time_seconds=12.0,
            videos=[StageVideo(path=Path(f"raw/v{n}.mp4"), role="primary")],
        )
        for n, name in ((1, "One"), (2, "Two"))
    ]
    legacy.save(shooter_root)
    audit_dir = legacy.audit_path(shooter_root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        _json.dumps({"shots": [], "audit_events": [{"ts": "2026-09-14T10:00:00Z", "kind": "save"}]}),
        encoding="utf-8",
    )
    user_config.record_project_open(match_root, match.name, kind="match")

    app = create_app()
    client = _MatchClient(app)
    resp = client.get("/api/me/recent-projects?detail=true")
    assert resp.status_code == 200
    by_kind = {p["kind"]: p for p in resp.json()["projects"]}
    assert by_kind["match"]["stages_audited"] == 1
    assert by_kind["match"]["next_step"] == {
        "kind": "audit",
        "shooter_slug": "ma",
        "stage_number": 2,
        "stage_name": "Two",
    }

    # Audit stage 2 too: nothing is auditable, so the next step is the
    # export -- unless a stage still lacks footage, which comes first.
    (audit_dir / "stage2.json").write_text(
        _json.dumps({"shots": [], "audit_events": [{"ts": "2026-09-14T10:01:00Z", "kind": "save"}]}),
        encoding="utf-8",
    )
    by_kind = {p["kind"]: p for p in client.get("/api/me/recent-projects?detail=true").json()["projects"]}
    assert by_kind["match"]["next_step"]["kind"] == "export"
    assert by_kind["match"]["next_step"]["shooter_slug"] == "ma"

    legacy = MatchProject.load(shooter_root)
    legacy.stages.append(StageEntry(stage_number=3, stage_name="Three", time_seconds=9.0, videos=[]))
    legacy.save(shooter_root)
    by_kind = {p["kind"]: p for p in client.get("/api/me/recent-projects?detail=true").json()["projects"]}
    assert by_kind["match"]["next_step"] == {
        "kind": "footage",
        "shooter_slug": "ma",
        "stage_number": 3,
        "stage_name": "Three",
    }


def test_recent_projects_detail_marks_missing_path(tmp_path: Path, _user_config_home: Path) -> None:
    from splitsmith import user_config

    ghost = tmp_path / "ghost"
    ghost.mkdir()
    user_config.record_project_open(ghost, "Ghost", kind="legacy")
    ghost.rmdir()

    app = create_app()
    client = _MatchClient(app)
    resp = client.get("/api/me/recent-projects?detail=true")
    kinds = [p["kind"] for p in resp.json()["projects"]]
    assert kinds == ["missing"]


def test_bind_match_folder_binds_match_root(tmp_path: Path, _user_config_home: Path) -> None:
    """Binding a Match folder binds the match itself, not a shooter subdir.

    Shooters are addressed per-request via slug-bearing URLs (#353).
    """
    from splitsmith import match_model

    target = tmp_path / "rich-match"
    match = match_model.Match.init(target, name="Rich Match")
    shooter = match_model.Shooter(slug="ma", name="Mathias")
    match.add_shooter(target, shooter)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bound"] is True
    assert body["project_root"] == str(target.resolve())


def test_user_config_disable_flag_makes_endpoints_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import user_config

    monkeypatch.setenv(user_config.ENV_DISABLE, "1")
    app = _match_create_app(project_root=tmp_path / "match", project_name="Disabled")
    client = _MatchClient(app)

    # Recording was a no-op.
    body = client.get("/api/me/recent-projects").json()
    assert body == {"projects": []}

    # PUT silently drops; GET still returns 200 null.
    client.put("/api/me/scoreboard-identity", json={"shooter_id": 1})
    cleared = client.get("/api/me/scoreboard-identity")
    assert cleared.status_code == 200
    assert cleared.json() is None


def test_recent_projects_detail_ignores_a_squadmate_without_footage(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """A shooter added before their footage must not sink the picker's
    audited count (#1613): the count reads the stages every filmed shooter
    finished, and the match stays in progress while someone still needs
    footage."""
    import json as _json

    from splitsmith import match_model, user_config

    match_root = tmp_path / "squad"
    match = match_model.Match.init(match_root, name="Squad")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(match_root)
    match.add_shooter(match_root, match_model.Shooter(slug="ma", name="Mathias"))
    match.add_shooter(match_root, match_model.Shooter(slug="qt", name="Quiet"))
    for slug, with_footage in (("ma", True), ("qt", False)):
        shooter_root = match_model.Match.shooter_root(match_root, slug)
        legacy = MatchProject.init(shooter_root, name="Squad")
        legacy.stages = [
            StageEntry(
                stage_number=n,
                stage_name=name,
                time_seconds=12.0,
                videos=[StageVideo(path=Path(f"raw/v{n}.mp4"), role="primary")] if with_footage else [],
            )
            for n, name in ((1, "One"), (2, "Two"))
        ]
        legacy.save(shooter_root)
    audit_dir = MatchProject.load(match_model.Match.shooter_root(match_root, "ma")).audit_path(
        match_model.Match.shooter_root(match_root, "ma")
    )
    audit_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        (audit_dir / f"stage{n}.json").write_text(
            _json.dumps({"shots": [], "audit_events": [{"ts": "2026-09-14T10:00:00Z", "kind": "save"}]}),
            encoding="utf-8",
        )
    user_config.record_project_open(match_root, match.name, kind="match")

    client = _MatchClient(create_app())
    by_kind = {p["kind"]: p for p in client.get("/api/me/recent-projects?detail=true").json()["projects"]}
    # Before the fix: (2 + 0) // 2 == 1.
    assert by_kind["match"]["stages_audited"] == 2
    assert by_kind["match"]["status"] == "in_progress"
    assert by_kind["match"]["next_step"]["kind"] == "footage"
    assert by_kind["match"]["next_step"]["shooter_slug"] == "qt"
