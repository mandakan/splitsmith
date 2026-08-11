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

import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from splitsmith import match_model, user_config
from splitsmith.match_project import MatchProject
from splitsmith.sync.client import HostedSyncClient
from splitsmith.ui import server as server_mod
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
        self.authorizes = 0
        self.polls = 0
        self.revokes = 0
        self.built_against: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/device/authorize":
            self.authorizes += 1
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
        fake.built_against.append(base_url)
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


def test_hosted_expired_verdict_is_a_terminal_state(tmp_path: Path, monkeypatch) -> None:
    """The hosted side itself can report ``expired`` (the device code's
    own TTL ran out on that end) - distinct from the local time-based
    expiry below, which never even reaches the hosted side."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([{"status": "expired"}])
    _install_fake(monkeypatch, fake)

    client.post(START)
    assert client.get(STATUS).json()["status"] == "expired"
    assert fake.polls == 1
    # Terminal: cleared, so the next read is idle rather than replaying.
    assert client.get(STATUS).json()["status"] == "idle"
    assert user_config.load_global_prefs().hosted_token is None


def test_local_time_based_expiry_clears_pending_state_without_polling(tmp_path: Path, monkeypatch) -> None:
    """The 10-minute window is enforced locally too, independent of what
    the hosted side would say - checked first, before any poll is
    forwarded, by reaching into AppState.device_flow directly rather than
    waiting out a real 10 minutes."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([{"status": "pending"}])
    _install_fake(monkeypatch, fake)

    client.post(START)
    state = client.app.state.splitsmith_state
    assert state.device_flow is not None
    state.device_flow["expires_at"] = time.monotonic() - 1.0

    assert client.get(STATUS).json()["status"] == "expired"
    assert fake.polls == 0  # expiry short-circuits before any hosted call
    assert state.device_flow is None
    assert user_config.load_global_prefs().hosted_token is None


def test_slow_down_remaps_to_pending(tmp_path: Path, monkeypatch) -> None:
    """The hosted side's slow_down verdict never reaches the SPA as a
    sixth state - the local side absorbs it and reports pending, exactly
    like a not-yet-decided poll."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([{"status": "slow_down"}]))

    client.post(START)
    resp = client.get(STATUS)
    assert resp.json()["status"] == "pending"
    # Not terminal: the flow is still alive for the next poll.
    assert client.app.state.splitsmith_state.device_flow is not None


def test_second_start_while_one_is_pending_resumes_it(tmp_path: Path, monkeypatch) -> None:
    """A second POST .../device/start must not silently clobber a still-
    live device_code: the first flow's code stays valid on the hosted
    side, so overwriting it locally would orphan it and leave the SPA
    polling a device_code the hosted side no longer expects a poll for.

    It must not dead-end either. The local process is the only holder of
    the live device_code, so refusing here (as this route used to, with
    409 device_login_already_pending) left an operator who cancelled the
    dialog with no code, no approve link and no way out short of waiting
    out the 10-minute TTL. Instead the still-live flow comes back, marked
    ``resumed``, with no second call to the hosted side.
    """
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([])
    _install_fake(monkeypatch, fake)

    first = client.post(START)
    assert first.status_code == 200, first.text
    assert first.json()["resumed"] is False

    second = client.post(START)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["resumed"] is True
    # Same flow: the user_code and both links are the first attempt's,
    # so the dialog can show the code and the approve button again.
    assert body["user_code"] == first.json()["user_code"] == "ABCD-2345"
    assert body["verification_uri"] == first.json()["verification_uri"]
    assert body["verification_uri_complete"] == first.json()["verification_uri_complete"]
    assert body["interval"] == 5
    # The remaining window, not a fresh 600s one, and still positive.
    assert 0 < body["expires_in"] <= 600
    # The hosted side was asked for exactly one authorization.
    assert fake.authorizes == 1
    # The secret never appears in a resume response either.
    assert "device_code" not in second.text


def test_start_after_the_pending_flow_expires_starts_a_fresh_one(tmp_path: Path, monkeypatch) -> None:
    """Resuming is only for a LIVE flow. Once the window is gone the code
    is unusable, so a start has to mint a new one rather than hand back a
    dead code the operator would type in vain."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([])
    _install_fake(monkeypatch, fake)

    client.post(START)
    state = client.app.state.splitsmith_state
    state.device_flow["expires_at"] = time.monotonic() - 1.0

    again = client.post(START)
    assert again.status_code == 200, again.text
    assert again.json()["resumed"] is False
    assert fake.authorizes == 2


def test_repointing_the_base_url_drops_a_pending_flow(tmp_path: Path, monkeypatch) -> None:
    """A live device_code only means something on the host it was minted
    on. Once the install is repointed, resuming that flow would send the
    operator to the old host's approve screen (and could link this
    install to an account on a server it no longer pushes to), so the
    pending flow goes with the URL."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([])
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.app.state.splitsmith_state.device_flow is not None

    client.put("/api/settings/hosted-sync", json={"base_url": "https://staging.hosted.example"})
    assert client.app.state.splitsmith_state.device_flow is None
    assert client.get(STATUS).json()["status"] == "idle"

    # And the next start is a fresh authorization against the new host,
    # not a resume of the stranded one.
    again = client.post(START)
    assert again.json()["resumed"] is False
    assert fake.authorizes == 2


def test_saving_a_pasted_token_clears_the_linked_account(tmp_path: Path, monkeypatch) -> None:
    """The cached account belongs to the credential that earned it.

    Pasting a token through the Advanced disclosure can point this
    install at a DIFFERENT account; leaving the old account block in
    place would make the chip assert an identity that no longer matches
    where the pushes go.
    """
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([dict(_APPROVED)]))
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"
    assert client.get("/api/settings/hosted-sync").json()["account"] is not None

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": "someone-elses-token"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"] is None
    assert client.get("/api/settings/hosted-sync").json()["account"] is None
    assert user_config.load_global_prefs().hosted_account is None


def test_repointing_the_base_url_clears_the_linked_account(tmp_path: Path, monkeypatch) -> None:
    """Prod to staging is a different account namespace. Same reasoning as
    the pasted-token case: the identity does not travel with the URL."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([dict(_APPROVED)]))
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"] is None
    assert user_config.load_global_prefs().hosted_account is None
    # The token is revoked and cleared on repoint (#737): a credential
    # minted by one host must not survive pointing this install at
    # another. Only same-host resubmits (``token: null``) keep it.
    assert user_config.load_global_prefs().hosted_token is None


def test_resaving_the_same_base_url_keeps_the_linked_account(tmp_path: Path, monkeypatch) -> None:
    """The other half of the clearing rule: a no-op save (the SPA
    resubmits base_url alone) must not sign the operator out of the chip.
    Without this, the clear above could be an unconditional wipe and no
    test would notice."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([dict(_APPROVED)]))
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"]["email"] == "shooter@example.com"
    assert user_config.load_global_prefs().hosted_account is not None


def test_repointing_the_base_url_revokes_and_clears_the_token(tmp_path: Path, monkeypatch) -> None:
    """A token minted by one host must not survive a repoint (#737): the
    revoke has to run against the OLD host, and the local copy goes."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert fake.revokes == 1
    # The revoke client was built against the OLD host, not the new one.
    assert fake.built_against[-1] == "https://hosted.example"
    assert resp.json()["token_set"] is False
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None


def test_repoint_revoke_failure_still_clears_the_token(tmp_path: Path, monkeypatch) -> None:
    """Old host unreachable: the local copy still goes. A dead token in
    config.yaml is the worse failure, same rule as sign-out."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)], revoke_status=500)
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
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


def test_sign_out_with_nothing_linked_reports_no_revoke_needed(tmp_path: Path, monkeypatch) -> None:
    """hosted_revoked is tri-state (#737): null means there was nothing to
    revoke, so the UI must not warn about a revoke that never ran."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([])
    _install_fake(monkeypatch, fake)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": None}
    assert fake.revokes == 0


def test_each_route_closes_its_hosted_client(tmp_path: Path, monkeypatch) -> None:
    """An httpx.Client per device-flow call that never gets closed leaks a
    connection pool - one device link polls roughly once per hosted
    interval for up to the 10-minute expiry window, so an abandoned
    login would leak dozens of clients in a long-running desktop
    server. Each of the three routes must close what it opens, success
    or failure."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([{"status": "pending"}])
    built: list[HostedSyncClient] = []

    def _build(base_url: str, *, token: str | None = None) -> HostedSyncClient:
        hosted = HostedSyncClient(
            http=httpx.Client(
                base_url=base_url,
                transport=httpx.MockTransport(fake.handle),
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
        )
        built.append(hosted)
        return hosted

    monkeypatch.setattr(server_mod, "_build_device_client", _build)

    client.post(START)
    client.get(STATUS)
    # Force a token into prefs so the unlink route's revoke branch
    # actually builds (and must close) a third client, rather than
    # skipping the branch entirely for want of a token.
    prefs = user_config.load_global_prefs()
    prefs.hosted_token = "fake-token"
    user_config.save_global_prefs(prefs)
    client.delete(SESSION)

    assert len(built) == 3, "expected one client per route call (start, status, unlink)"
    assert all(c._http.is_closed for c in built), "every device-flow client must be closed"


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
