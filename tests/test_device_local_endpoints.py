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
from collections.abc import Callable
from datetime import UTC, datetime
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
        # The bearer whoami is expected to see. Defaults to the token the
        # ``_link_account`` fixture writes; the two device-flow tests that
        # link with a *different* token (the one the approval verdict
        # itself scripts) override this before the call that reaches
        # whoami with it (#877 review wave 2) -- a hard-coded constant
        # here rejected a legitimate token and the resulting
        # AssertionError was silently swallowed by the refresh's own
        # broad except, leaving the refresh path unexercised.
        self.expected_bearer = "desktop-token"
        self.authorizes = 0
        self.polls = 0
        self.revokes = 0
        self.built_against: list[str] = []
        self.built_kwargs: list[dict] = []
        self.whoami_calls = 0
        # dict | None picks the "not set" default (empty body); anything
        # else (list, str, ...) scripts a 200 whose body is not the shape
        # SyncWhoAmIResponse declares (#877 review).
        self.whoami_payload: dict | list | str | None = None
        self.whoami_status = 200
        self.whoami_auth: list[str | None] = []
        # Fires while the refresh is parked in the threadpool, i.e. the
        # window in which another writer can touch config.yaml (#877
        # review).
        self.whoami_hook: Callable[[], None] | None = None

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
        if path == "/api/sync/whoami":
            self.whoami_calls += 1
            self.whoami_auth.append(request.headers.get("authorization"))
            # The route is authenticated by the desktop token; a refresh
            # that stopped sending it would 401 in production and degrade
            # silently to the cached snapshot -- #877's own bug, back and
            # invisible unless the double insists on the credential.
            expected = f"Bearer {self.expected_bearer}"
            assert (
                self.whoami_auth[-1] == expected
            ), f"whoami reached the hosted side with the wrong bearer: {self.whoami_auth[-1]!r}"
            if self.whoami_hook is not None:
                self.whoami_hook()
            if self.whoami_status != 200:
                return httpx.Response(self.whoami_status, json={"detail": "nope"})
            body = self.whoami_payload if self.whoami_payload is not None else {}
            return httpx.Response(200, json=body)
        raise AssertionError(f"unexpected hosted call: {path}")


def _install_fake(monkeypatch, fake: _FakeHosted) -> None:
    def _build(base_url: str, *, token: str | None = None, timeout: float = 30.0) -> HostedSyncClient:
        fake.built_against.append(base_url)
        fake.built_kwargs.append({"token": token, "timeout": timeout})
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
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)

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

    # The GET below refreshes the cached snapshot, which builds its
    # hosted client with the token approval just linked -- not
    # "desktop-token" (#877 review wave 2). A matching whoami payload
    # keeps the refresh a clean no-op (the account is unchanged) rather
    # than tripping the response-shape ValidationError the double's
    # empty default body would otherwise cause -- that would also be
    # silently swallowed, and this test is not the one about that path.
    fake.expected_bearer = "sync-token-value"
    fake.whoami_payload = {"id": "u1", "email": "shooter@example.com", "display_name": None}
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
    # Strict: a resumed start counts down the REMAINDER. A hardcoded 600
    # would pass <=; the strict bound is what makes this falsifiable.
    assert 0 < body["expires_in"] < 600
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
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"
    # The GET below refreshes the cached snapshot, which builds its
    # hosted client with the token approval just linked -- not
    # "desktop-token" (#877 review wave 2). A matching whoami payload
    # keeps the refresh a clean no-op (the account is unchanged) rather
    # than tripping the response-shape ValidationError the double's
    # empty default body would otherwise cause -- that would also be
    # silently swallowed, and this test is not the one about that path.
    fake.expected_bearer = "sync-token-value"
    fake.whoami_payload = {"id": "u1", "email": "shooter@example.com", "display_name": None}
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


def test_repoint_with_new_token_revokes_old_then_stores_new(tmp_path: Path, monkeypatch) -> None:
    """Repoint and paste a new token in one PUT: the OLD token is revoked
    against the OLD host first, then the new token is stored for the new
    host - the revoke client must not be built with the new credentials."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": "fresh-token"},
    )
    assert resp.status_code == 200, resp.text
    assert fake.revokes == 1
    # The revoke client was built against the OLD host, not the new one.
    assert fake.built_against[-1] == "https://hosted.example"
    assert resp.json()["token_set"] is True
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token == "fresh-token"
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


# The cached account snapshot refreshes itself (#877)


def _link_account(
    tmp_path: Path, monkeypatch, hosted: _FakeHosted, *, base_url: str = "https://hosted.example"
) -> TestClient:
    """A local app with config.yaml already holding a linked account.

    Written directly rather than driven through the device flow: this
    file already covers the flow, and these tests are about what happens
    to the snapshot afterwards.

    ``base_url`` is overridable so a malformed one (#877 review) can be
    written straight into config.yaml the way a hand-edited file would -
    ``GlobalPrefs.hosted_base_url`` is a bare ``str`` with no format
    validation anywhere.

    The double is built with the ``token`` the route passes, as a real
    ``_build_device_client`` does, and records every kwarg: a fixture
    that drops them cannot see a refresh lose its credential or its
    timeout (#877 review).
    """
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    prefs = user_config.load_global_prefs()
    prefs.hosted_base_url = base_url
    prefs.hosted_token = "desktop-token"
    prefs.hosted_account = user_config.HostedAccountRef(
        id="u1",
        email="owner@example.com",
        display_name=None,
        device_name="mac studio",
        linked_at=datetime.now(UTC),
    )
    user_config.save_global_prefs(prefs)
    client = _local_app(tmp_path)
    _install_fake(monkeypatch, hosted)
    return client


def test_settings_picks_up_a_display_name_set_on_the_web(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)

    body = client.get("/api/settings/hosted-sync").json()

    # Asserted first, and separately from the outcome: the route is
    # authenticated, so a refresh that stopped sending the bearer would
    # 401 in production and the broad except would degrade silently to
    # the cached snapshot -- #877 itself, back. Left to the outcome
    # assertion alone, the only symptom is a display_name that stayed
    # None, which reads as a dozen other things (#877 review).
    assert hosted.whoami_auth == ["Bearer desktop-token"]
    # ...and it sits in front of a UI paint, so it uses the short budget
    # rather than the 30s the device-flow calls take.
    assert hosted.built_kwargs == [
        {"token": "desktop-token", "timeout": server_mod.HOSTED_ACCOUNT_REFRESH_TIMEOUT_S}
    ]
    assert server_mod.HOSTED_ACCOUNT_REFRESH_TIMEOUT_S == 5.0

    assert body["account"]["display_name"] == "Mathias A"
    assert hosted.whoami_calls == 1
    # and it survives a restart, i.e. it reached config.yaml
    assert user_config.load_global_prefs().hosted_account.display_name == "Mathias A"


def test_settings_picks_up_an_email_change_too(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "new@example.com", "display_name": None}
    client = _link_account(tmp_path, monkeypatch, hosted)

    assert client.get("/api/settings/hosted-sync").json()["account"]["email"] == "new@example.com"


def test_an_upstream_failure_returns_the_cached_account(tmp_path: Path, monkeypatch) -> None:
    # #738: a transient failure must not make a linked operator look
    # unlinked. The chip reads a missing account as "sign in".
    hosted = _FakeHosted([])
    hosted.whoami_status = 503
    client = _link_account(tmp_path, monkeypatch, hosted)

    resp = client.get("/api/settings/hosted-sync")

    assert resp.status_code == 200
    assert resp.json()["account"]["email"] == "owner@example.com"


def test_a_401_does_not_unlink_the_device(tmp_path: Path, monkeypatch) -> None:
    # Deliberate: auto-unlinking on an upstream status code would mean a
    # hosted outage signs every desktop install out. Revocation still
    # surfaces on the next sync.
    hosted = _FakeHosted([])
    hosted.whoami_status = 401
    client = _link_account(tmp_path, monkeypatch, hosted)

    assert client.get("/api/settings/hosted-sync").json()["account"] is not None
    assert user_config.load_global_prefs().hosted_token == "desktop-token"


def test_a_second_call_inside_the_ttl_does_not_hit_upstream(tmp_path: Path, monkeypatch) -> None:
    # GlobalBar and the mobile drawer each render a chip with independent
    # state, and both refetch on route changes. Without the TTL a desktop
    # session issues a steady trickle of upstream calls for a label.
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)

    client.get("/api/settings/hosted-sync")
    client.get("/api/settings/hosted-sync")

    assert hosted.whoami_calls == 1


def test_a_failed_refresh_also_consumes_the_ttl(tmp_path: Path, monkeypatch) -> None:
    # Otherwise every chip mount retries against a dead host and blocks
    # for the timeout each time.
    hosted = _FakeHosted([])
    hosted.whoami_status = 503
    client = _link_account(tmp_path, monkeypatch, hosted)

    client.get("/api/settings/hosted-sync")
    client.get("/api/settings/hosted-sync")

    assert hosted.whoami_calls == 1


def test_an_unchanged_response_does_not_rewrite_config(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": None}
    client = _link_account(tmp_path, monkeypatch, hosted)
    config_path = user_config.user_config_dir() / user_config.CONFIG_FILENAME
    before = config_path.stat().st_mtime_ns

    client.get("/api/settings/hosted-sync")

    assert config_path.stat().st_mtime_ns == before


def test_an_unlinked_install_makes_no_upstream_call(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    client = _local_app(tmp_path)
    _install_fake(monkeypatch, hosted)

    assert client.get("/api/settings/hosted-sync").json()["account"] is None
    assert hosted.whoami_calls == 0


def test_a_signout_underneath_an_in_flight_refresh_is_not_reverted(tmp_path: Path, monkeypatch) -> None:
    """The refresh is a WRITER on the one route the SPA fires by itself.

    It loads prefs, awaits whoami (up to 5s), then saves. If it saves
    the object it loaded before the await, it persists the whole
    ``GlobalPrefs`` - including ``hosted_token`` - and a sign-out that
    landed in that window is undone: a revoked bearer and a cleared
    account link are both back in config.yaml, and the chip renders the
    account again on the next mount. That is #738 in reverse (#877
    review).
    """
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)
    seen: dict = {}

    def _sign_out_now() -> None:
        # The refresh is parked in the threadpool right here; the
        # operator clicks sign out.
        seen["unlink"] = client.delete(SESSION).json()
        seen["disk_after_unlink"] = user_config.load_global_prefs()

    hosted.whoami_hook = _sign_out_now

    body = client.get("/api/settings/hosted-sync").json()
    disk = user_config.load_global_prefs()

    # The sign-out itself did its job...
    assert seen["unlink"] == {"cleared": True, "hosted_revoked": True}
    assert seen["disk_after_unlink"].hosted_token is None
    assert seen["disk_after_unlink"].hosted_account is None
    # ...and the refresh that finished afterwards left it alone.
    assert disk.hosted_token is None, "SIGN-OUT REVERTED: revoked token is back on disk"
    assert disk.hosted_account is None, "SIGN-OUT REVERTED: account is back on disk"
    # The response agrees with disk rather than reporting the stale
    # snapshot the request started from.
    assert body["account"] is None
    assert body["token_set"] is False


def test_a_relink_to_another_account_underneath_a_refresh_wins(tmp_path: Path, monkeypatch) -> None:
    """The stale response must not be merged onto a different account.

    Same window as above, but config.yaml now holds a *different*
    hosted account. The in-flight answer describes the old one, so it
    is discarded outright rather than stamped onto the new link.
    """
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)

    def _relink_now() -> None:
        prefs = user_config.load_global_prefs()
        prefs.hosted_account = user_config.HostedAccountRef(
            id="u2",
            email="other@example.com",
            display_name="Someone Else",
            device_name="thinkpad",
            linked_at=datetime.now(UTC),
        )
        user_config.save_global_prefs(prefs)

    hosted.whoami_hook = _relink_now

    body = client.get("/api/settings/hosted-sync").json()

    assert body["account"]["email"] == "other@example.com"
    assert body["account"]["display_name"] == "Someone Else"
    disk = user_config.load_global_prefs()
    assert disk.hosted_account.id == "u2"
    assert disk.hosted_account.email == "other@example.com"


def test_a_malformed_base_url_returns_the_cached_account(tmp_path: Path, monkeypatch) -> None:
    # httpx.InvalidURL is NOT an httpx.HTTPError subclass, and
    # hosted_base_url is a bare str with no format validation anywhere -
    # a hand-edited config.yaml reaches this. Must degrade to the cached
    # snapshot, not 500 a route whose whole job is to report it (#877
    # review).
    hosted = _FakeHosted([])
    client = _link_account(tmp_path, monkeypatch, hosted, base_url="http://[::1")

    resp = client.get("/api/settings/hosted-sync")

    assert resp.status_code == 200
    assert resp.json()["account"]["email"] == "owner@example.com"
    # The URL never resolves to a request, so the fake never sees a call.
    assert hosted.whoami_calls == 0


def test_a_non_dict_whoami_body_returns_the_cached_account(tmp_path: Path, monkeypatch) -> None:
    # A 200 whose JSON body isn't the SyncWhoAmIResponse shape (a bare
    # list here) must degrade to the cached snapshot rather than raise
    # out of unvalidated dict access (#877 review).
    hosted = _FakeHosted([])
    hosted.whoami_payload = []
    client = _link_account(tmp_path, monkeypatch, hosted)

    resp = client.get("/api/settings/hosted-sync")

    assert resp.status_code == 200
    assert resp.json()["account"]["email"] == "owner@example.com"
    assert hosted.whoami_calls == 1
