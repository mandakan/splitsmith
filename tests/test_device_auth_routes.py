"""HTTP surface for the browser-assisted device flow (#719).

Task 2 covers the state machine in isolation; this file exercises it
wired into real routes, including the two auth boundaries the routes
themselves own: the public poll pair (no cookie, no bearer) and the
session-cookie approval pair.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hosted_helpers import PUBLIC_URL, _CapturingSender, login


def _authorize(client: TestClient, name: str = "mac studio") -> dict:
    resp = client.post("/api/device/authorize", json={"device_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_authorize_is_public_and_returns_both_urls(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)

    assert body["device_code"]
    assert len(body["user_code"]) == 9 and body["user_code"][4] == "-"
    # Full URL, not just the suffix: the origin is the whole point of
    # _public_base. SPLITSMITH_PUBLIC_URL (set to PUBLIC_URL by the
    # hosted_app fixture) is what makes the approve link correct behind a
    # proxy, where request.base_url is the internal address the operator
    # cannot reach. A suffix-only assertion passes either way.
    assert body["verification_uri"] == f"{PUBLIC_URL}/desktop/approve"
    assert body["verification_uri_complete"] == (f"{PUBLIC_URL}/desktop/approve?code={body['user_code']}")
    assert body["expires_in"] == 600
    assert body["interval"] == 5


def test_poll_before_approval_is_pending(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)
    resp = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["token"] is None


def test_unknown_device_code_reports_expired_not_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Uniform verdict: a caller must not be able to probe for live codes."""
    client, _ = hosted_app
    resp = client.post("/api/device/token", json={"device_code": "nope"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"


def test_pending_screen_requires_a_session(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)
    assert client.get(f"/api/device/pending/{body['user_code']}").status_code == 401


def test_approve_then_poll_returns_the_token_and_account(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")

    pending = client.get(f"/api/device/pending/{body['user_code']}")
    assert pending.status_code == 200, pending.text
    assert pending.json()["device_name"] == "mac studio"
    assert pending.json()["scope"] == "sync"

    approve = client.post(f"/api/device/pending/{body['user_code']}/approve")
    assert approve.status_code == 200, approve.text

    poll = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert poll.status_code == 200, poll.text
    payload = poll.json()
    assert payload["status"] == "approved"
    assert payload["token"]
    assert payload["account"]["email"] == "owner@example.com"
    assert payload["device_name"] == "mac studio"


def test_second_poll_after_collection_reports_expired(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")
    client.post("/api/device/token", json={"device_code": body["device_code"]})

    again = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert again.json()["status"] == "expired"
    assert again.json()["token"] is None


def test_deny_then_poll_reports_denied(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    assert client.post(f"/api/device/pending/{body['user_code']}/deny").status_code == 200

    poll = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert poll.json()["status"] == "denied"


def test_pending_screen_404s_for_an_already_decided_code(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")

    assert client.get(f"/api/device/pending/{body['user_code']}").status_code == 404


def test_user_code_lookup_is_case_and_hyphen_insensitive(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The operator retypes the code off another screen; be forgiving."""
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")

    typed = body["user_code"].replace("-", "").lower()
    assert client.get(f"/api/device/pending/{typed}").status_code == 200


def test_device_session_delete_revokes_the_calling_token(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")
    token = client.post("/api/device/token", json={"device_code": body["device_code"]}).json()["token"]
    client.cookies.clear()  # isolate the bearer token; the session cookie
    # from login() above would otherwise win in CompositeAuth and mask
    # whatever the bearer alone would resolve to (see test_token_scope_gate.py).

    headers = {"Authorization": f"Bearer {token}"}
    assert client.delete("/api/device/session", headers=headers).status_code == 200
    # The credential is dead: the same bearer now fails auth outright.
    assert (
        client.post("/api/sync/matches", json={"match_id": "m1", "name": "x"}, headers=headers).status_code
        == 401
    )


def test_device_routes_404_in_local_mode(tmp_path) -> None:
    """Same hosted-gate idiom as sync_api: a local install has no accounts
    to authorize against, so the whole surface is simply absent.

    The body matters as much as the status here. Three different things
    produce a 404 on these paths and only one of them is the guard under
    test: ``_hosted_gate`` says ``{"detail": "not found"}``, the SPA
    catch-all says ``{"detail": "api route not found"}`` (which is what a
    checkout with no built bundle would fall through to if the route
    itself had vanished), and FastAPI's own no-such-route says
    ``{"detail": "Not Found"}``. Asserting only the number lets this pass
    for the wrong reason.
    """
    from splitsmith import match_model
    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Local")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    client = TestClient(create_app(project_root=root, project_name="Local"))
    authorize = client.post("/api/device/authorize", json={"device_name": "x"})
    assert authorize.status_code == 404
    assert authorize.json() == {"detail": "not found"}
    poll = client.post("/api/device/token", json={"device_code": "x"})
    assert poll.status_code == 404
    assert poll.json() == {"detail": "not found"}
