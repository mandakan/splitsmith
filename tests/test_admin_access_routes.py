"""Admin API for access requests and account tiers (spec 2026-10-03)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


@pytest.fixture
def admin_app(hosted_env: str, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "boss@x.se")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    state = app.state.splitsmith_state
    state.auth.backends[0]._email = sender
    state.email_sender = sender
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender, state


def seed_request(state, email: str) -> str:  # noqa: ANN001
    asyncio.run(state.access_requests.record(email, source="form"))
    return next(r.id for r in asyncio.run(state.access_requests.list()) if r.email == email)


def test_non_admin_is_403(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "friend@x.se")
    assert client.get("/api/admin/access-requests").status_code == 403
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/access-tiers").status_code == 403
    assert client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "full"}).status_code == 403
    assert client.post(f"/api/admin/access-requests/{rid}/decline").status_code == 403
    assert client.post(f"/api/admin/access-requests/{rid}/resend").status_code == 403
    assert client.patch("/api/admin/users/x", json={"access_tier": "full"}).status_code == 403
    assert asyncio.run(state.access_requests.get(rid)).status == "pending"


def test_list_requests_with_status_filter(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    seed_request(state, "a@x.se")
    rid_b = seed_request(state, "b@x.se")
    login(client, sender, "boss@x.se")
    client.post(f"/api/admin/access-requests/{rid_b}/decline")
    assert [r["email"] for r in client.get("/api/admin/access-requests").json()] == ["a@x.se", "b@x.se"]
    pending = client.get("/api/admin/access-requests", params={"status": "pending"}).json()
    assert [r["email"] for r in pending] == ["a@x.se"]


def test_approve_sends_a_working_link_with_the_tier(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    resp = client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "sharing"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["tier_granted"], body["decided_by"]) == ("approved", "sharing", "boss@x.se")
    assert body["email_sent_at"] is not None
    [(to, link)] = sender.granted
    assert to == "erik@x.se"
    client.cookies.clear()
    token = link.split("token=")[1]
    assert client.get("/auth/callback", params={"token": token}).status_code == 303
    me = client.get("/api/me").json()
    assert (me["email"], me["access_tier"]) == ("erik@x.se", "sharing")


def test_second_decide_is_409(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    assert client.post(f"/api/admin/access-requests/{rid}/decline").status_code == 200
    assert client.post(f"/api/admin/access-requests/{rid}/decline").status_code == 409
    # A declined request can still be approved; approving twice is a 409.
    assert client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "full"}).status_code == 200
    assert client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "full"}).status_code == 409
    assert len(sender.granted) == 1


def test_unknown_request_is_404(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "boss@x.se")
    assert client.post("/api/admin/access-requests/nope/approve", json={"tier": "full"}).status_code == 404
    assert client.post("/api/admin/access-requests/nope/decline").status_code == 404
    assert client.post("/api/admin/access-requests/nope/resend").status_code == 404


def test_unknown_tier_is_422(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    assert client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "gold"}).status_code == 422
    assert asyncio.run(state.access_requests.get(rid)).status == "pending"
    assert sender.granted == []
    uid = client.get("/api/admin/users").json()[0]["id"]
    assert client.patch(f"/api/admin/users/{uid}", json={"access_tier": "gold"}).status_code == 422
    assert client.get("/api/admin/users").json()[0]["access_tier"] == "full"


def test_mail_failure_keeps_the_approval_and_resend_works(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    original = sender.send_access_granted

    async def boom(**_kw: object) -> None:
        raise RuntimeError("provider down")

    sender.send_access_granted = boom
    resp = client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "sharing"})
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["email_sent_at"]) == ("approved", None)
    sender.send_access_granted = original
    again = client.post(f"/api/admin/access-requests/{rid}/resend")
    assert again.status_code == 200 and again.json()["email_sent_at"] is not None
    assert len(sender.granted) == 1


def test_resend_on_a_pending_or_declined_request_is_409(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    assert client.post(f"/api/admin/access-requests/{rid}/resend").status_code == 409
    client.post(f"/api/admin/access-requests/{rid}/decline")
    assert client.post(f"/api/admin/access-requests/{rid}/resend").status_code == 409
    assert sender.granted == []


def test_users_list_and_tier_change(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "friend@x.se")
    client.cookies.clear()
    login(client, sender, "boss@x.se")
    users = {u["email"]: u for u in client.get("/api/admin/users").json()}
    assert users["boss@x.se"]["is_admin"] is True
    assert users["friend@x.se"]["is_admin"] is False
    expected_keys = {"id", "email", "display_name", "access_tier", "created_at", "is_admin"}
    assert set(users["friend@x.se"]) == expected_keys
    fid = users["friend@x.se"]["id"]
    changed = client.patch(f"/api/admin/users/{fid}", json={"access_tier": "sharing"}).json()
    assert (changed["access_tier"], changed["is_admin"]) == ("sharing", False)
    assert client.patch("/api/admin/users/nope", json={"access_tier": "full"}).status_code == 404


def test_tiers_endpoint(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "boss@x.se")
    body = client.get("/api/admin/access-tiers").json()
    assert body["default_tier"] == "full"
    tiers = {t["name"]: t["features"] for t in body["tiers"]}
    assert set(tiers) == {"full", "sharing", "disabled"}
    assert sorted(tiers["sharing"]) == ["share", "sync"]
    assert tiers["disabled"] == []
    assert sorted(tiers["full"]) == sorted(["sync", "share", "create_match", "raw_upload", "hosted_compute"])


def test_routes_404_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        assert client.get("/api/admin/access-requests").status_code == 404
        assert client.post("/api/admin/access-requests/x/approve", json={"tier": "full"}).status_code == 404
        assert client.post("/api/admin/access-requests/x/decline").status_code == 404
        assert client.post("/api/admin/access-requests/x/resend").status_code == 404
        assert client.get("/api/admin/users").status_code == 404
        assert client.patch("/api/admin/users/x", json={"access_tier": "full"}).status_code == 404
        assert client.get("/api/admin/access-tiers").status_code == 404
