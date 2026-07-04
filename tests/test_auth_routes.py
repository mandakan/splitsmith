"""HTTP-surface tests for the magic-link auth routes (auth-swap PR2b).

In-process via TestClient against a SQLite-backed hosted app. The app's
``MagicLinkAuth`` e-mail sender is swapped for a capturing double so the
test can read the token the console transport would only log, then drive
the real ``/auth/callback`` redemption + cookie round-trip. The docker
smoke proves the same dance cross-process against Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


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


# ---------------------------------------------------------------------------
# is_admin field on /api/me
# ---------------------------------------------------------------------------


def test_is_admin_false_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local mode: /api/me always returns is_admin false (empty frozenset)."""
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    monkeypatch.delenv("SPLITSMITH_ADMIN_EMAILS", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is False


def test_is_admin_false_when_no_allowlist(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosted user with no SPLITSMITH_ADMIN_EMAILS set: is_admin false."""
    monkeypatch.delenv("SPLITSMITH_ADMIN_EMAILS", raising=False)
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "user@example.com")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is False


def test_is_admin_true_when_in_allowlist(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosted user whose email is in SPLITSMITH_ADMIN_EMAILS: is_admin true."""
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "admin@example.com,other@example.com")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "admin@example.com")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is True


def test_is_admin_case_insensitive(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Email comparison is case-insensitive on both sides."""
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "Admin@Example.com")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        # magic-link lowercases on sign-in, but belt-and-suspenders: the
        # stored email is lowercase; the env-var entry has mixed case.
        login(client, sender, "admin@example.com")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is True


def test_is_admin_false_for_non_admin_hosted_user(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosted user whose email is NOT in SPLITSMITH_ADMIN_EMAILS: is_admin false."""
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "other@example.com")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "notadmin@example.com")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is False
