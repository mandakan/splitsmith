"""HTTP-surface tests for the desktop-token management routes (#631).

Tests GET/POST/DELETE /api/me/desktop-tokens - the owner-facing surface the
hosted account UI (Task 10) calls to mint / list / revoke desktop-to-hosted
sync bearer tokens. Task 2 already covers DesktopTokenStore/DesktopTokenAuth
in isolation (tests/test_desktop_tokens.py); this file exercises them wired
into real HTTP routes, including the bearer-auth gate end to end.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login

URL = "/api/me/desktop-tokens"


def _url(token_id: str) -> str:
    return f"{URL}/{token_id}"


# anonymous requests are rejected


def test_anonymous_get_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.get(URL).status_code == 401


def test_anonymous_post_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.post(URL, json={"name": "mac studio"}).status_code == 401


def test_anonymous_delete_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.delete(_url("no-such-id")).status_code == 401


# create: raw token returned once, record has no hash/raw


def test_post_creates_token_and_returns_raw_once(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    resp = client.post(URL, json={"name": "mac studio"})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["token"]
    record = body["record"]
    assert record["name"] == "mac studio"
    assert record["id"]
    assert record["created_at"]
    assert record["last_used_at"] is None
    assert record["revoked_at"] is None
    assert "hash" not in record
    assert "token_hash" not in record
    assert "token" not in record


# list: shows the record, never the hash/raw


def test_get_lists_the_created_token_without_hash_or_raw(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    create = client.post(URL, json={"name": "mac studio"})
    assert create.status_code == 201
    token_id = create.json()["record"]["id"]

    list_resp = client.get(URL)
    assert list_resp.status_code == 200
    tokens = list_resp.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["id"] == token_id
    assert tokens[0]["name"] == "mac studio"
    assert "hash" not in tokens[0]
    assert "token_hash" not in tokens[0]
    assert "token" not in tokens[0]


# revoke: true, and the bearer stops authenticating afterward


def test_revoke_returns_true_and_bearer_stops_authenticating(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    create = client.post(URL, json={"name": "mac studio"})
    assert create.status_code == 201
    raw = create.json()["token"]
    token_id = create.json()["record"]["id"]

    # The bearer alone (no session cookie) authenticates against a gated
    # /api/me/* route - same gate Task 2's DesktopTokenAuth wired in.
    client.cookies.clear()
    bearer_headers = {"Authorization": f"Bearer {raw}"}
    before = client.get(URL, headers=bearer_headers)
    assert before.status_code == 200
    assert len(before.json()["tokens"]) == 1

    # Revoke via a normal owner session (the SPA's account page, not the
    # bearer itself - the bearer has no route to revoke through).
    login(client, sender, "owner@example.com")
    revoke = client.delete(_url(token_id))
    assert revoke.status_code == 200
    assert revoke.json() == {"revoked": True}

    # The now-revoked bearer stops authenticating.
    client.cookies.clear()
    after = client.get(URL, headers=bearer_headers)
    assert after.status_code == 401


def test_revoke_is_idempotent(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    token_id = client.post(URL, json={"name": "t"}).json()["record"]["id"]
    assert client.delete(_url(token_id)).json() == {"revoked": True}
    assert client.delete(_url(token_id)).json() == {"revoked": True}


def test_revoke_unknown_id_returns_false(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    resp = client.delete(_url("no-such-id"))
    assert resp.status_code == 200
    assert resp.json() == {"revoked": False}


# cross-user isolation


def test_second_user_cannot_list_or_revoke_first_users_token(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "usera@example.com")
    token_id = client.post(URL, json={"name": "a's token"}).json()["record"]["id"]

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    list_resp = client.get(URL)
    assert list_resp.status_code == 200
    assert list_resp.json()["tokens"] == []

    revoke_resp = client.delete(_url(token_id))
    assert revoke_resp.status_code == 200
    assert revoke_resp.json() == {"revoked": False}

    # User A's token still lists for user A, unrevoked.
    client.cookies.clear()
    login(client, sender, "usera@example.com")
    a_tokens = client.get(URL).json()["tokens"]
    assert len(a_tokens) == 1
    assert a_tokens[0]["id"] == token_id
    assert a_tokens[0]["revoked_at"] is None


# local mode: no desktop-token surface


def test_local_mode_404() -> None:
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get(URL).status_code == 404
        assert client.post(URL, json={"name": "x"}).status_code == 404
        assert client.delete(_url("no-such-id")).status_code == 404
