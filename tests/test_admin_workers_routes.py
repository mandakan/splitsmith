"""HTTP-surface tests for admin worker management endpoints (Task 7).

GET    /api/admin/workers          - list all workers
POST   /api/admin/workers          - create a self-hosted worker
PATCH  /api/admin/workers/{id}     - update worker fields
DELETE /api/admin/workers/{id}     - delete a worker

All routes require admin authentication (403 for non-admin).
In local mode all routes return 404 (workers surface does not exist off-hosted).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from splitsmith.db import create_engine, sessionmaker
from splitsmith.db.workers import WorkersStore
from tests.hosted_helpers import PUBLIC_URL, _CapturingSender, login

ADMIN_EMAIL = "admin@example.com"
USER_EMAIL = "user@example.com"
NOT_FOUND = {"detail": "not found"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _with_store(db_url: str, fn):
    """Run one WorkersStore operation on a throwaway engine, then dispose it."""

    async def _run():
        engine = create_engine(db_url)
        try:
            return await fn(WorkersStore(sessionmaker(engine)))
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@pytest.fixture
def admin_client(
    hosted_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, object]]:
    """Hosted app logged in as an admin user."""
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", ADMIN_EMAIL)
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, ADMIN_EMAIL)
        yield client, app


# ---------------------------------------------------------------------------
# Local mode: all admin routes return 404
# ---------------------------------------------------------------------------


def test_local_mode_list_is_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.get("/api/admin/workers")
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


def test_local_mode_create_is_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.post("/api/admin/workers", json={"name": "box"})
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


def test_local_mode_patch_is_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.patch("/api/admin/workers/anything", json={"enabled": False})
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


def test_local_mode_delete_is_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.delete("/api/admin/workers/anything")
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


# ---------------------------------------------------------------------------
# Auth gate: 401 for unauthenticated, 403 for non-admin
# ---------------------------------------------------------------------------


def test_unauthenticated_gets_401(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", ADMIN_EMAIL)
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.get("/api/admin/workers")
        assert resp.status_code == 401


def test_non_admin_gets_403(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", ADMIN_EMAIL)
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, USER_EMAIL)
        resp = client.get("/api/admin/workers")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/workers
# ---------------------------------------------------------------------------


def test_admin_list_empty(admin_client) -> None:
    client, _ = admin_client
    resp = client.get("/api/admin/workers")
    assert resp.status_code == 200
    assert resp.json() == {"workers": []}


def test_admin_list_returns_workers_after_create(admin_client) -> None:
    client, _ = admin_client
    client.post("/api/admin/workers", json={"name": "list-box", "priority": 7})
    resp = client.get("/api/admin/workers")
    assert resp.status_code == 200
    workers = resp.json()["workers"]
    assert len(workers) == 1
    assert workers[0]["name"] == "list-box"
    assert workers[0]["priority"] == 7


# ---------------------------------------------------------------------------
# POST /api/admin/workers
# ---------------------------------------------------------------------------


def test_admin_create_happy_path(admin_client) -> None:
    client, _ = admin_client
    resp = client.post("/api/admin/workers", json={"name": "my-box", "priority": 5})
    assert resp.status_code == 200
    body = resp.json()
    w = body["worker"]
    assert w["name"] == "my-box"
    assert w["priority"] == 5
    assert w["kind"] == "self_hosted"
    assert w["enabled"] is True
    assert w["status"] == "pending"
    assert isinstance(body["registration_token"], str) and body["registration_token"]
    assert isinstance(body["expires_at"], str) and body["expires_at"]


def test_admin_create_docker_command_contains_url_image_token(admin_client) -> None:
    client, _ = admin_client
    resp = client.post("/api/admin/workers", json={"name": "docker-box"})
    assert resp.status_code == 200
    body = resp.json()
    cmd = body["docker_command"]
    token = body["registration_token"]
    assert "docker run" in cmd
    assert PUBLIC_URL in cmd
    assert token in cmd
    assert "agent" in cmd
    # named container so the UI's `docker logs -f splitsmith-agent` step resolves
    assert "--name splitsmith-agent" in cmd
    # default image
    assert "ghcr.io/mandakan/splitsmith" in cmd


def test_admin_create_custom_image_from_env(
    admin_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLITSMITH_AGENT_IMAGE", "myregistry/splitsmith:edge")
    client, _ = admin_client
    resp = client.post("/api/admin/workers", json={"name": "custom-img-box"})
    assert resp.status_code == 200
    assert "myregistry/splitsmith:edge" in resp.json()["docker_command"]


def test_admin_create_default_priority(admin_client) -> None:
    client, _ = admin_client
    resp = client.post("/api/admin/workers", json={"name": "default-prio"})
    assert resp.status_code == 200
    assert resp.json()["worker"]["priority"] == 10


def test_admin_create_duplicate_name_409(admin_client) -> None:
    client, _ = admin_client
    assert client.post("/api/admin/workers", json={"name": "dup-box"}).status_code == 200
    resp = client.post("/api/admin/workers", json={"name": "dup-box"})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/admin/workers/{worker_id}
# ---------------------------------------------------------------------------


def test_admin_delete_unknown_404(admin_client) -> None:
    client, _ = admin_client
    resp = client.delete("/api/admin/workers/no-such-id")
    assert resp.status_code == 404


def test_admin_delete_railway_400(admin_client, hosted_env: str) -> None:
    """Deleting the singleton railway row is refused with 400."""
    railway_id = _with_store(
        hosted_env,
        lambda s: _ensure_railway_id(s),
    )
    client, _ = admin_client
    resp = client.delete(f"/api/admin/workers/{railway_id}")
    assert resp.status_code == 400


def test_admin_delete_self_hosted_returns_204(admin_client) -> None:
    client, _ = admin_client
    create_resp = client.post("/api/admin/workers", json={"name": "to-delete"})
    worker_id = create_resp.json()["worker"]["id"]
    resp = client.delete(f"/api/admin/workers/{worker_id}")
    assert resp.status_code == 204


def test_admin_delete_pushes_disabled_to_connected_channel(admin_client) -> None:
    """DELETE pushes 'disabled' to a connected channel before removing the row."""
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "delete-with-channel"})
    worker_id = create_resp.json()["worker"]["id"]

    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    client.delete(f"/api/admin/workers/{worker_id}")

    assert not q.empty()
    assert q.get_nowait() == "disabled"


# ---------------------------------------------------------------------------
# PATCH /api/admin/workers/{worker_id}
# ---------------------------------------------------------------------------


def test_admin_patch_not_found_404(admin_client) -> None:
    client, _ = admin_client
    resp = client.patch("/api/admin/workers/no-such-id", json={"enabled": False})
    assert resp.status_code == 404


def test_admin_patch_updates_enabled(admin_client) -> None:
    client, _ = admin_client
    create_resp = client.post("/api/admin/workers", json={"name": "patch-enable"})
    worker_id = create_resp.json()["worker"]["id"]
    resp = client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["status"] == "disabled"


def test_admin_patch_enabled_false_pushes_disabled_to_channel(admin_client) -> None:
    """PATCH enabled=False pushes 'disabled' to a connected channel."""
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "channel-disable"})
    worker_id = create_resp.json()["worker"]["id"]

    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    resp = client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert not q.empty()
    assert q.get_nowait() == "disabled"

    del registry._channels[worker_id]


def test_admin_patch_enabled_true_pushes_enabled_to_channel(admin_client) -> None:
    """PATCH enabled=True (from disabled) pushes 'enabled' to a connected channel."""
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "channel-enable"})
    worker_id = create_resp.json()["worker"]["id"]
    client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": False})

    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    resp = client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": True})
    assert resp.status_code == 200
    assert not q.empty()
    assert q.get_nowait() == "enabled"

    del registry._channels[worker_id]


def test_admin_patch_same_enabled_no_push(admin_client) -> None:
    """PATCH with the same enabled value does not push to the channel."""
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "no-push-worker"})
    worker_id = create_resp.json()["worker"]["id"]

    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    # enabled is already True; patching with True should not push
    resp = client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": True})
    assert resp.status_code == 200
    assert q.empty()

    del registry._channels[worker_id]


def test_admin_patch_priority_and_name(admin_client) -> None:
    client, _ = admin_client
    create_resp = client.post("/api/admin/workers", json={"name": "rename-me", "priority": 5})
    worker_id = create_resp.json()["worker"]["id"]
    resp = client.patch(f"/api/admin/workers/{worker_id}", json={"name": "renamed", "priority": 20})
    assert resp.status_code == 200
    w = resp.json()
    assert w["name"] == "renamed"
    assert w["priority"] == 20


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def test_status_pending_when_not_registered_not_connected(admin_client) -> None:
    client, _ = admin_client
    resp = client.post("/api/admin/workers", json={"name": "status-pending"})
    assert resp.json()["worker"]["status"] == "pending"


def test_status_disabled_overrides_when_enabled_false(admin_client) -> None:
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "status-disabled"})
    worker_id = create_resp.json()["worker"]["id"]
    client.patch(f"/api/admin/workers/{worker_id}", json={"enabled": False})

    # Even if "connected", disabled wins
    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    list_resp = client.get("/api/admin/workers")
    w = next(x for x in list_resp.json()["workers"] if x["id"] == worker_id)
    assert w["status"] == "disabled"

    del registry._channels[worker_id]


def test_status_online_when_connected_and_enabled(admin_client) -> None:
    client, app = admin_client
    registry = app.state.splitsmith_state.wake_channels

    create_resp = client.post("/api/admin/workers", json={"name": "status-online"})
    worker_id = create_resp.json()["worker"]["id"]

    q: asyncio.Queue = asyncio.Queue()
    registry._channels[worker_id] = q

    list_resp = client.get("/api/admin/workers")
    w = next(x for x in list_resp.json()["workers"] if x["id"] == worker_id)
    assert w["status"] == "online"

    del registry._channels[worker_id]


def test_status_offline_when_registered_and_not_connected(admin_client, hosted_env: str) -> None:
    """A registered but disconnected self-hosted worker shows as offline."""
    client, _ = admin_client

    create_resp = client.post("/api/admin/workers", json={"name": "status-offline"})
    worker_id = create_resp.json()["worker"]["id"]
    reg_token = create_resp.json()["registration_token"]

    # Complete registration via the worker endpoint
    client.post("/api/workers/register", json={"token": reg_token})

    list_resp = client.get("/api/admin/workers")
    w = next(x for x in list_resp.json()["workers"] if x["id"] == worker_id)
    assert w["status"] == "offline"
    assert w["registered"] is True


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _ensure_railway_id(store: WorkersStore) -> str:
    await store.ensure_railway_row()
    rows = await store.list()
    return next(r for r in rows if r.kind == "railway").id
