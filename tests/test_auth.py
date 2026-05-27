"""Tests for the auth abstraction and the /api/me endpoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from splitsmith.auth import LOOPBACK_USER_EMAIL, LOOPBACK_USER_ID, LoopbackAuth, User
from splitsmith.ui.server import create_app


class _AnonymousAuth:
    """Test double: an auth backend that resolves every request to
    anonymous. Stands in for the hosted-mode case where no cookie
    is present -- the dep should 401 before the handler runs."""

    async def authenticate_request(self, request: Request) -> User | None:
        return None


def test_loopback_auth_returns_singleton_user() -> None:
    backend = LoopbackAuth()

    # The request is intentionally ignored -- LoopbackAuth never reads
    # headers or cookies. Pass None to make that contract explicit.
    user = asyncio.run(backend.authenticate_request(None))  # type: ignore[arg-type]

    assert isinstance(user, User)
    assert user.id == LOOPBACK_USER_ID
    assert user.email == LOOPBACK_USER_EMAIL


def test_api_me_returns_loopback_user(tmp_path: Path) -> None:
    # Scaffold a Match folder so the bind path is happy (Tier 1
    # step 3 of doc 10 retired legacy single-shooter scaffolding).
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Auth Test Match")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    MatchProject.init(match_model.Match.shooter_root(root, "me"), name="Auth Test Match")

    app = create_app(project_root=root, project_name="Auth Test Match")
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


# Every /api/me/* route should be gated by the get_current_user dep,
# so swapping the backend to one that returns anonymous should make
# them all 401. Each entry is (method, template, concrete_url) -- the
# template is what FastAPI registers; the concrete URL is what the
# TestClient actually hits. ``test_me_route_coverage_matches_app``
# below fails if a new route lands without an entry here.
_ME_ROUTES_REQUIRING_AUTH: list[tuple[str, str, str]] = [
    ("GET", "/api/me", "/api/me"),
    ("GET", "/api/me/jobs", "/api/me/jobs"),
    ("GET", "/api/me/jobs/{job_id}", "/api/me/jobs/does-not-exist"),
    ("POST", "/api/me/jobs/acknowledge-failures", "/api/me/jobs/acknowledge-failures"),
    ("POST", "/api/me/jobs/{job_id}/acknowledge", "/api/me/jobs/does-not-exist/acknowledge"),
    ("POST", "/api/me/jobs/{job_id}/cancel", "/api/me/jobs/does-not-exist/cancel"),
    ("GET", "/api/me/recent-projects", "/api/me/recent-projects"),
    ("POST", "/api/me/recent-projects/forget", "/api/me/recent-projects/forget"),
    ("POST", "/api/me/recent-projects/bind", "/api/me/recent-projects/bind"),
    ("POST", "/api/me/recent-projects/unbind", "/api/me/recent-projects/unbind"),
    ("GET", "/api/me/scoreboard-identity", "/api/me/scoreboard-identity"),
    ("PUT", "/api/me/scoreboard-identity", "/api/me/scoreboard-identity"),
    ("DELETE", "/api/me/scoreboard-identity", "/api/me/scoreboard-identity"),
    ("POST", "/api/me/projects/import", "/api/me/projects/import"),
    ("POST", "/api/me/raw/upload", "/api/me/raw/upload"),
    ("GET", "/api/me/raw/list", "/api/me/raw/list"),
    ("DELETE", "/api/me/raw/{filename:path}", "/api/me/raw/clip.mp4"),
]


@pytest.mark.parametrize(
    "method,url",
    [(m, url) for m, _, url in _ME_ROUTES_REQUIRING_AUTH],
)
def test_anonymous_request_gets_401_on_me_routes(method: str, url: str) -> None:
    app = create_app()
    app.state.splitsmith_state.auth = _AnonymousAuth()
    client = TestClient(app)

    resp = client.request(method, url)

    # 401 means the auth dep fired before the handler. Anything else
    # (404, 422, 200) means the handler ran first and the dep was
    # bypassed.
    assert resp.status_code == 401, (
        f"{method} {url} returned {resp.status_code}, expected 401 -- "
        "did this route forget Depends(get_current_user)?"
    )


def test_me_route_coverage_matches_app() -> None:
    """Guard rail: every registered /api/me/* route appears in the
    401 parametrize list above. Catches new routes that ship without
    the auth gate.
    """
    app = create_app()
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not path.startswith("/api/me"):
            continue
        for method in methods:
            if method == "HEAD":
                continue
            registered.add((method, path))

    covered = {(m, tmpl) for m, tmpl, _ in _ME_ROUTES_REQUIRING_AUTH}
    missing = registered - covered
    assert not missing, f"new /api/me/* routes not covered by the 401 gate test: {sorted(missing)}"


# The auth gate middleware sits in front of every /api/* route, not
# just /api/me/*. Picking a few representative non-/api/me endpoints
# proves the middleware (rather than per-route Depends) is what does
# the gating. If the middleware regresses, ``/api/me/*`` would still
# pass via Depends and only these would fail -- which is the point.
_NON_ME_AUTHED_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/project"),
    ("GET", "/api/jobs"),
    ("GET", "/api/fs/list"),
    ("GET", "/api/stages/1/audit"),
]


@pytest.mark.parametrize("method,url", _NON_ME_AUTHED_ROUTES)
def test_anonymous_request_gets_401_on_non_me_routes(method: str, url: str) -> None:
    app = create_app()
    app.state.splitsmith_state.auth = _AnonymousAuth()
    client = TestClient(app)

    resp = client.request(method, url)

    assert resp.status_code == 401, (
        f"{method} {url} returned {resp.status_code}, expected 401 -- "
        "did the _auth_gate middleware regress?"
    )


# Paths the gate must let through anonymously. Health + features are
# read before any user is established; ``/api/shutdown`` carries its
# own loopback gate and answers 202 / 403 on its own terms. The list
# is duplicated from ``_PUBLIC_API_PATHS`` on purpose -- this test
# fails loudly if someone trims the allowlist without thinking about
# what depends on it.
_PUBLIC_PATHS: list[tuple[str, str]] = [
    ("GET", "/api/health"),
    ("GET", "/api/server/features"),
]


@pytest.mark.parametrize("method,url", _PUBLIC_PATHS)
def test_public_paths_bypass_auth_gate(method: str, url: str) -> None:
    app = create_app()
    app.state.splitsmith_state.auth = _AnonymousAuth()
    client = TestClient(app)

    resp = client.request(method, url)

    # Anything but 401 is fine -- the handler's own response (200,
    # 4xx, etc.) just proves auth didn't short-circuit it.
    assert resp.status_code != 401, f"{method} {url} returned 401; the public allowlist should let it through"


def test_non_api_paths_bypass_auth_gate() -> None:
    """SPA static assets (and anything not under /api/*) are served
    without an auth check -- auth lives at the API layer."""
    app = create_app()
    app.state.splitsmith_state.auth = _AnonymousAuth()
    client = TestClient(app)

    # The SPA may or may not be built in the test env; either way the
    # auth gate should not be the one rejecting the request.
    resp = client.get("/")

    assert resp.status_code != 401
