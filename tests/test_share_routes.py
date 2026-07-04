"""HTTP-surface tests for the share-token management routes (issue #349).

Tests the GET/POST/DELETE /api/match/shares routes via the
/api/matches/{match_id}/match/shares alias prefix.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login, seed_match

MID = "test-match-abc123"
OTHER_MID = "test-match-xyz999"


def _url(match_id: str, suffix: str = "") -> str:
    return f"/api/matches/{match_id}/match/shares{suffix}"


# Anonymous requests (no session cookie) are rejected with 401.


def test_anonymous_get_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.get(_url(MID)).status_code == 401


def test_anonymous_post_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.post(_url(MID)).status_code == 401


def test_anonymous_delete_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.delete(_url(MID, "/some-id")).status_code == 401


# POST creates a share: 201, url starts with public_base_url/share/, revoked_at is None.


def test_post_creates_share(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    resp = client.post(_url(MID))
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"].startswith("http://localhost:5174/share/")
    assert body["revoked_at"] is None
    assert "id" in body
    assert "created_at" in body


# GET lists shares; after DELETE the revoked share is still returned with revoked_at set.


def test_get_lists_shares_and_includes_revoked(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    create_resp = client.post(_url(MID))
    assert create_resp.status_code == 201
    share_id = create_resp.json()["id"]

    list_resp = client.get(_url(MID))
    assert list_resp.status_code == 200
    shares = list_resp.json()["shares"]
    assert len(shares) == 1
    assert shares[0]["id"] == share_id
    assert shares[0]["revoked_at"] is None

    # Revoke via DELETE, then list - revoked share is still present.
    del_resp = client.delete(_url(MID, f"/{share_id}"))
    assert del_resp.status_code == 204

    list_after = client.get(_url(MID))
    shares_after = list_after.json()["shares"]
    assert len(shares_after) == 1
    assert shares_after[0]["revoked_at"] is not None


# DELETE is idempotent: second call on the same share_id returns 204.


def test_delete_is_idempotent(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    share_id = client.post(_url(MID)).json()["id"]
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204


# DELETE with an unknown share_id returns 404.


def test_delete_unknown_share_id_returns_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    assert client.delete(_url(MID, "/no-such-id")).status_code == 404


# POST against a match_id not owned by the user returns 404 (alias middleware ownership gate).


def test_post_unowned_match_id_returns_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    # No MatchRow for OTHER_MID - the alias middleware returns 404.
    assert client.post(_url(OTHER_MID)).status_code == 404


# User B cannot list user A's shares (alias middleware blocks by ownership).


def test_user_b_cannot_list_user_a_shares(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app

    login(client, sender, "usera@example.com")
    seed_match(hosted_env, "usera@example.com", MID)
    client.post(_url(MID))  # user A creates a share

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    # User B doesn't own MID - alias middleware returns 404.
    assert client.get(_url(MID)).status_code == 404


# User B cannot revoke user A's shares (alias middleware blocks by ownership).


def test_user_b_cannot_revoke_user_a_share(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app

    login(client, sender, "usera@example.com")
    seed_match(hosted_env, "usera@example.com", MID)
    share_id = client.post(_url(MID)).json()["id"]

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    # User B doesn't own MID - alias middleware returns 404.
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 404
