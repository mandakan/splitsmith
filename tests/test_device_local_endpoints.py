"""Local-side device flow: start, poll, and unlink (#719).

The hosted server is an ``httpx.MockTransport`` double, wired in by
monkeypatching ``server._build_device_client`` - the one seam the three
routes build their client through. No network, no hosted app.

What matters here and is easy to get wrong:
  - the token is written to config.yaml on approval and NEVER echoed back
  - the account block appears in GET /api/settings/hosted-sync
  - polling is throttled server-side, so a fast-refreshing SPA cannot
    trip the hosted side's slow_down
  - sign-out clears local prefs even when the hosted revoke call fails
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from splitsmith import match_model, user_config
from splitsmith.sync.client import HostedSyncClient
from splitsmith.ui import server as server_mod
from splitsmith.ui.project import MatchProject
from splitsmith.ui.server import create_app
from tests.hosted_helpers import _CapturingSender, login

START = "/api/settings/hosted-sync/device/start"
STATUS = "/api/settings/hosted-sync/device/status"
SESSION = "/api/settings/hosted-sync/session"


def _local_app(tmp_path: Path) -> TestClient:
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Device Test")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    MatchProject.init(match_model.Match.shooter_root(root, "me"), name="Device Test")
    return TestClient(create_app(project_root=root, project_name="Device Test"))


class _FakeHosted:
    """Scripted hosted side. ``verdicts`` is popped one per poll."""

    def __init__(self, verdicts: list[dict], *, revoke_status: int = 200) -> None:
        self.verdicts = verdicts
        self.revoke_status = revoke_status
        self.polls = 0
        self.revokes = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/device/authorize":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code",
                    "user_code": "ABCD-2345",
                    "verification_uri": "https://hosted.example/desktop/approve",
                    "verification_uri_complete": ("https://hosted.example/desktop/approve?code=ABCD-2345"),
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/api/device/token":
            self.polls += 1
            verdict = self.verdicts.pop(0) if self.verdicts else {"status": "expired"}
            return httpx.Response(200, json=verdict)
        if path == "/api/device/session":
            self.revokes += 1
            return httpx.Response(self.revoke_status, json={"revoked": True})
        raise AssertionError(f"unexpected hosted call: {path}")


def _install_fake(monkeypatch, fake: _FakeHosted) -> None:
    def _build(base_url: str, *, token: str | None = None) -> HostedSyncClient:
        return HostedSyncClient(
            http=httpx.Client(
                base_url=base_url,
                transport=httpx.MockTransport(fake.handle),
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
        )

    monkeypatch.setattr(server_mod, "_build_device_client", _build)


_APPROVED = {
    "status": "approved",
    "token": "sync-token-value",
    "account": {"id": "u1", "email": "shooter@example.com", "display_name": None},
    "device_name": "gaspode",
}


def test_start_requires_a_configured_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    resp = client.post(START)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "hosted_base_url_not_set"


def test_start_returns_the_user_code_but_never_the_device_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([]))

    resp = client.post(START)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_code"] == "ABCD-2345"
    assert body["verification_uri_complete"].endswith("code=ABCD-2345")
    assert "device_code" not in resp.text


def test_status_is_idle_before_a_flow_starts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    assert client.get(STATUS).json()["status"] == "idle"


def test_approval_writes_token_and_account_without_echoing_the_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([dict(_APPROVED)]))

    client.post(START)
    status = client.get(STATUS)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "approved"
    assert status.json()["account"]["email"] == "shooter@example.com"
    assert "sync-token-value" not in status.text

    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token == "sync-token-value"
    assert prefs.hosted_account is not None
    assert prefs.hosted_account.email == "shooter@example.com"
    assert prefs.hosted_account.device_name == "gaspode"

    settings = client.get("/api/settings/hosted-sync")
    assert settings.json()["token_set"] is True
    assert settings.json()["account"]["email"] == "shooter@example.com"
    assert "sync-token-value" not in settings.text


def test_status_throttles_to_the_hosted_interval(tmp_path: Path, monkeypatch) -> None:
    """The SPA polls faster than the hosted interval on purpose; the local
    side absorbs that instead of tripping slow_down upstream."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([{"status": "pending"}, {"status": "pending"}, {"status": "pending"}])
    _install_fake(monkeypatch, fake)

    client.post(START)
    for _ in range(3):
        assert client.get(STATUS).json()["status"] == "pending"
    assert fake.polls == 1


def test_denied_and_expired_are_distinct_terminal_states(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([{"status": "denied"}]))

    client.post(START)
    assert client.get(STATUS).json()["status"] == "denied"
    # Terminal: the pending state is cleared, so the next read is idle.
    assert client.get(STATUS).json()["status"] == "idle"
    assert user_config.load_global_prefs().hosted_token is None


def test_sign_out_clears_prefs_and_revokes_hosted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    client.get(STATUS)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": True}
    assert fake.revokes == 1
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None
    # base_url survives: it is how the operator points at staging.
    assert prefs.hosted_base_url == "https://hosted.example"


def test_sign_out_clears_prefs_even_when_the_hosted_revoke_fails(tmp_path: Path, monkeypatch) -> None:
    """Leaving a dead token in config.yaml because the network was down
    is the worse failure. The flag is what lets the UI say so."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)], revoke_status=500)
    _install_fake(monkeypatch, fake)
    client.post(START)
    client.get(STATUS)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": False}
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None


def test_device_routes_404_in_hosted_mode(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Local-only, the inverse of the /api/device/* guard.

    Must log in first: an unauthenticated request never reaches the
    route's own ``_hosted_mode_active()`` check - the global auth-gate
    middleware 401s first for any path outside its public allowlist.
    Without this the test would pass on a 401 and never exercise the
    guard it names.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(START).status_code == 404
    assert client.get(STATUS).status_code == 404
    assert client.delete(SESSION).status_code == 404
