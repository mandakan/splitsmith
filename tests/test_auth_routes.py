"""HTTP-surface tests for the magic-link auth routes (auth-swap PR2b).

In-process via TestClient against a SQLite-backed hosted app. The app's
``MagicLinkAuth`` e-mail sender is swapped for a capturing double so the
test can read the token the console transport would only log, then drive
the real ``/auth/callback`` redemption + cookie round-trip. The docker
smoke proves the same dance cross-process against Postgres.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from splitsmith.db import Base, create_engine

PUBLIC_URL = "http://localhost:5174"


class _CapturingSender:
    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []

    async def send_magic_link(self, *, to: str, link: str) -> None:
        self.links.append((to, link))

    def last_token(self) -> str:
        return parse_qs(urlparse(self.links[-1][1]).query)["token"][0]


@pytest.fixture
def hosted_env(tmp_path: Path) -> Iterator[str]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth_routes.sqlite'}"
    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    prior = {
        k: os.environ.get(k) for k in ("SPLITSMITH_DATABASE_URL", "SPLITSMITH_MODE", "SPLITSMITH_PUBLIC_URL")
    }
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    os.environ["SPLITSMITH_PUBLIC_URL"] = PUBLIC_URL
    try:
        yield url
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def hosted_app(hosted_env: str) -> Iterator[tuple[TestClient, _CapturingSender]]:
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    # Swap the console transport for the capturing double so the test can
    # read the emitted token.
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender


def test_full_login_logout_round_trip(hosted_app) -> None:
    client, sender = hosted_app

    # Anonymous is rejected.
    assert client.get("/api/me").status_code == 401

    # Begin: always 200, e-mails a link.
    begin = client.post("/api/v1/auth/begin", json={"email": "Person@Example.com"})
    assert begin.status_code == 200
    assert begin.json() == {"ok": True}
    assert sender.links and sender.links[-1][0] == "person@example.com"

    # Redeem the token at the callback: 303 to "/", sets the session cookie.
    callback = client.get("/auth/callback", params={"token": sender.last_token()})
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    assert "splitsmith_session" in callback.cookies

    # The cookie now authenticates /api/me.
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"

    # Logout revokes the session + clears the cookie.
    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    # Cookie cleared -> anonymous again.
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_callback_rejects_bad_token_without_cookie(hosted_app) -> None:
    client, _ = hosted_app
    resp = client.get("/auth/callback", params={"token": "not-a-real-token"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=invalid_link"
    assert "splitsmith_session" not in resp.cookies


def test_callback_token_is_single_use(hosted_app) -> None:
    client, sender = hosted_app
    client.post("/api/v1/auth/begin", json={"email": "once@example.com"})
    token = sender.last_token()

    first = client.get("/auth/callback", params={"token": token})
    assert first.status_code == 303 and first.headers["location"] == "/"
    client.cookies.clear()
    second = client.get("/auth/callback", params={"token": token})
    assert second.headers["location"] == "/login?error=invalid_link"


def test_begin_rejects_invalid_email(hosted_app) -> None:
    client, _ = hosted_app
    assert client.post("/api/v1/auth/begin", json={"email": "  "}).status_code == 400
    assert client.post("/api/v1/auth/begin", json={"email": "no-at-sign"}).status_code == 400


def test_begin_is_public_but_logout_requires_auth(hosted_app) -> None:
    client, _ = hosted_app
    # begin reachable anonymously (it's in the public allowlist).
    assert client.post("/api/v1/auth/begin", json={"email": "x@example.com"}).status_code == 200
    # logout is auth-gated -> 401 without a session.
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_auth_routes_404_in_local_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local mode has no login flow: the routes exist but 404, and /api/me
    resolves the loopback user without any cookie."""
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        assert client.post("/api/v1/auth/begin", json={"email": "x@example.com"}).status_code == 404
        assert client.get("/auth/callback", params={"token": "x"}).status_code == 404
        # Loopback: /api/me works with no cookie.
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["id"] == "local"
