"""Tests for the auth abstraction and the /api/me endpoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith.auth import LOOPBACK_USER_EMAIL, LOOPBACK_USER_ID, LoopbackAuth, User
from splitsmith.ui.server import create_app


def test_loopback_auth_returns_singleton_user() -> None:
    backend = LoopbackAuth()

    # The request is intentionally ignored -- LoopbackAuth never reads
    # headers or cookies. Pass None to make that contract explicit.
    user = asyncio.run(backend.authenticate_request(None))  # type: ignore[arg-type]

    assert isinstance(user, User)
    assert user.id == LOOPBACK_USER_ID
    assert user.email == LOOPBACK_USER_EMAIL


def test_api_me_returns_loopback_user(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="Auth Test Match")
    client = TestClient(app)

    resp = client.get("/api/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == LOOPBACK_USER_ID
    assert body["email"] == LOOPBACK_USER_EMAIL


def test_api_me_works_when_unbound() -> None:
    """Auth resolves above the bound-project check -- the picker
    page needs to know the operator before any project exists."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.json()["id"] == LOOPBACK_USER_ID
