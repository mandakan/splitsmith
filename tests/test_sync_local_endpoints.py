"""Tests for the local sync wiring: settings endpoints, the trigger/status
routes, and the ``sync_match`` job (Task 9 of the desktop-to-hosted sync
MVP, #631).

All four routes - ``GET``/``PUT /api/settings/hosted-sync``,
``POST /api/match/sync``, ``GET /api/match/sync/status`` - are local-only:
they 404 in hosted mode (the inverse of the hosted-only guard the share
and desktop-token routes use). The settings routes are process-global
(not match-scoped); the trigger/status routes are match-scoped and
reached via the ``/api/matches/{match_id}/`` alias, same as
``/api/match/shares``.

No real network: the "trigger enqueues a job" test points the configured
base_url at a closed local port so the job body's HTTP call fails fast in
the background without the test waiting on it - the test only asserts
the synchronous enqueue response (``Job.kind``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from splitsmith import match_model, user_config
from splitsmith.match_project import MatchProject
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id
from tests.hosted_helpers import _CapturingSender, login, seed_match

# A closed local port: connection is refused immediately, so a job body
# that tries to reach it fails fast instead of hanging on a DNS timeout.
_UNREACHABLE_BASE_URL = "http://127.0.0.1:9"


def _local_app_with_match(tmp_path: Path) -> tuple[TestClient, str]:
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Sync Test Match")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    MatchProject.init(match_model.Match.shooter_root(root, "me"), name="Sync Test Match")
    app = create_app(project_root=root, project_name="Sync Test Match")
    client = TestClient(app)
    match_id = bound_match_id(app)
    return client, match_id


# ---------------------------------------------------------------------------
# GET/PUT /api/settings/hosted-sync
# ---------------------------------------------------------------------------


def test_settings_round_trip_masks_token(tmp_path: Path) -> None:
    client, _ = _local_app_with_match(tmp_path)

    initial = client.get("/api/settings/hosted-sync")
    assert initial.status_code == 200
    assert initial.json() == {"base_url": None, "token_set": False, "account": None}

    put = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )
    assert put.status_code == 200
    body = put.json()
    assert body == {"base_url": "https://hosted.example", "token_set": True, "account": None}
    assert "token" not in body
    assert "secret-token" not in put.text

    # Persisted for real, even though it's never echoed back.
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_base_url == "https://hosted.example"
    assert prefs.hosted_token == "secret-token"

    again = client.get("/api/settings/hosted-sync")
    assert again.json() == {"base_url": "https://hosted.example", "token_set": True, "account": None}


def test_first_save_accepts_a_base_url_with_no_token(tmp_path: Path) -> None:
    """A fresh install must be able to set the hosted target alone (#719).

    The device flow refuses to start until ``hosted_base_url`` is set,
    and this route is the only thing that sets it - so requiring a token
    here would mean pasting one before the flow that exists to replace
    pasting one could even be reached.
    """
    client, _ = _local_app_with_match(tmp_path)

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"base_url": "https://hosted.example", "token_set": False, "account": None}

    assert user_config.load_global_prefs().hosted_base_url == "https://hosted.example"
    assert client.get("/api/settings/hosted-sync").json()["base_url"] == "https://hosted.example"


def test_settings_put_null_token_keeps_stored_token(tmp_path: Path) -> None:
    client, _ = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example/v2", "token": None},
    )
    assert resp.status_code == 200
    assert resp.json() == {"base_url": "https://hosted.example/v2", "token_set": True, "account": None}
    assert user_config.load_global_prefs().hosted_token == "secret-token"


def test_settings_put_empty_token_clears_stored_token(tmp_path: Path) -> None:
    client, _ = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": ""},
    )
    assert resp.status_code == 200
    assert resp.json() == {"base_url": "https://hosted.example", "token_set": False, "account": None}
    assert user_config.load_global_prefs().hosted_token is None


# ---------------------------------------------------------------------------
# POST /api/match/sync
# ---------------------------------------------------------------------------


def test_sync_trigger_without_config_returns_409(tmp_path: Path) -> None:
    client, match_id = _local_app_with_match(tmp_path)

    resp = client.post(f"/api/matches/{match_id}/match/sync")

    assert resp.status_code == 409
    assert resp.json() == {"detail": "sync_not_configured"}


def test_sync_trigger_with_config_enqueues_job(tmp_path: Path) -> None:
    client, match_id = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": _UNREACHABLE_BASE_URL, "token": "secret-token"},
    )

    resp = client.post(f"/api/matches/{match_id}/match/sync")

    assert resp.status_code == 200
    job = resp.json()
    assert job["kind"] == "sync_match"
    assert job["status"] in ("pending", "running")


# ---------------------------------------------------------------------------
# GET /api/match/sync/status
# ---------------------------------------------------------------------------


def test_status_on_never_synced_configured_match(tmp_path: Path) -> None:
    client, match_id = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )

    resp = client.get(f"/api/matches/{match_id}/match/sync/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["last_synced_at"] is None
    assert body["stale"] is True
    assert isinstance(body["pending_media"], int)
    assert body["errors"] == []


def test_status_unconfigured_match_reports_not_configured(tmp_path: Path) -> None:
    client, match_id = _local_app_with_match(tmp_path)

    resp = client.get(f"/api/matches/{match_id}/match/sync/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["stale"] is True


def test_sync_status_reports_remote_changes(tmp_path: Path, monkeypatch) -> None:
    """A manifest with a doc version doc_versions has not seen -> remote_changes 1."""
    client, match_id = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )
    manifest = [
        {
            "doc_kind": "project",
            "slug": "anna",
            "stage_number": None,
            "version": 5,
            "updated_at": "2026-08-10T10:00:00+00:00",
        }
    ]
    monkeypatch.setattr("splitsmith.ui.server._fetch_remote_manifest", lambda prefs, match_id: manifest)

    resp = client.get(f"/api/matches/{match_id}/match/sync/status")

    assert resp.status_code == 200
    assert resp.json()["remote_changes"] == 1


def test_sync_status_offline_remote_changes_none(tmp_path: Path, monkeypatch) -> None:
    client, match_id = _local_app_with_match(tmp_path)
    client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "secret-token"},
    )

    def _boom(prefs, match_id):
        raise httpx.TransportError("offline")

    monkeypatch.setattr("splitsmith.ui.server._fetch_remote_manifest", _boom)

    resp = client.get(f"/api/matches/{match_id}/match/sync/status")

    assert resp.status_code == 200
    assert resp.json()["remote_changes"] is None


# ---------------------------------------------------------------------------
# Hosted mode: all four routes 404
# ---------------------------------------------------------------------------


def test_all_four_routes_404_in_hosted_mode(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    mid = "sync-local-endpoints-mid"
    seed_match(hosted_env, "owner@example.com", mid)

    assert client.get("/api/settings/hosted-sync").status_code == 404
    assert client.put("/api/settings/hosted-sync", json={"base_url": "x", "token": "y"}).status_code == 404
    assert client.post(f"/api/matches/{mid}/match/sync").status_code == 404
    assert client.get(f"/api/matches/{mid}/match/sync/status").status_code == 404
