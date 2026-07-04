"""HTTP-surface tests for the share-token management routes (issue #349).

Tests the GET/POST/DELETE /api/match/shares routes via the
/api/matches/{match_id}/match/shares alias prefix.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import ProjectStateStore, ShareTokenRow, User, create_engine, sessionmaker
from tests.hosted_helpers import _CapturingSender, login, seed_match

MID = "test-match-abc123"
OTHER_MID = "test-match-xyz999"
SLUG = "anna"


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


# ----------------------------------------------------------------------
# Task 5: anonymous, token-authorized read path (_share_alias middleware)
# ----------------------------------------------------------------------

NOT_FOUND = {"detail": "not found"}


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the read handlers
    load, as the user identified by ``user_email`` (call after login)."""
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=match_id,
            name=f"Test match {match_id}",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Anna")
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


def _expire_token(db_url: str, token: str) -> None:
    """Force a share token's ``expires_at`` into the past."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _expire() -> None:
        async with sf() as s:
            row = (await s.execute(_select(ShareTokenRow).where(ShareTokenRow.token == token))).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await s.commit()

    asyncio.run(_expire())


def _create_share_token(client: TestClient, match_id: str) -> str:
    """Create a share via the owner management route and return the raw token."""
    resp = client.post(_url(match_id))
    assert resp.status_code == 201, f"share create failed: {resp.status_code} {resp.text}"
    return resp.json()["url"].rsplit("/share/", 1)[1]


def _share_url(token: str, rest: str) -> str:
    return f"/api/share/{token}/{rest}"


def _setup_shared_match(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> str:
    """Login as owner, seed a match + shooter + state docs, mint a share
    token, then drop the session cookie so the client is anonymous. Returns
    the raw token."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share_token(client, MID)
    client.cookies.clear()
    return token


# -- uniform 404s --------------------------------------------------------


def test_share_unknown_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    resp = client.get(_share_url("garbage-token", "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND
    assert not client.cookies


def test_share_revoked_token_404_on_every_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    create = client.post(_url(MID))
    token = create.json()["url"].rsplit("/share/", 1)[1]
    share_id = create.json()["id"]
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204
    client.cookies.clear()

    for rest in (
        "match/shooters",
        f"shooters/{SLUG}/project",
        f"shooters/{SLUG}/stages/1/coach",
        f"shooters/{SLUG}/videos/stream",
    ):
        resp = client.get(_share_url(token, rest))
        assert resp.status_code == 404, rest
        assert resp.json() == NOT_FOUND, rest


def test_share_expired_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    _expire_token(hosted_env, token)
    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


@pytest.mark.parametrize(
    "rest",
    [
        "match",  # no such whitelisted rest
        f"shooters/{SLUG}/videos",  # prefix of a whitelisted path
        "me",
        f"shooters/{SLUG}/project/extra",
    ],
)
def test_share_valid_token_non_whitelisted_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    rest: str,
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, rest))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_share_whitelisted_non_get_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.post(_share_url(token, "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


# -- happy paths ---------------------------------------------------------


def test_share_match_shooters_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    slugs = [entry["slug"] for entry in body["shooters"]]
    assert slugs == [SLUG]
    # Authorization was the token alone - no session cookie was set.
    assert not client.cookies


def test_share_project_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Anna"
    assert not client.cookies


def test_share_url_match_id_is_ignored(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The match read is driven entirely by the token row; the URL carries
    no match id, so there is no URL surface to influence which match loads.
    A second, unshared match owned by the same user stays unreachable."""
    token = _setup_shared_match(hosted_env, hosted_app)
    # Seed a second, un-shared match owned by the same owner.
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", OTHER_MID)
    _seed_state_docs(hosted_env, "owner@example.com", OTHER_MID, "bob")
    client.cookies.clear()
    # The share token resolves to MID (shooter "anna"), never OTHER_MID.
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200, resp.text
    slugs = [entry["slug"] for entry in resp.json()["shooters"]]
    assert slugs == [SLUG]


# -- local mode: no share surface ---------------------------------------


def test_share_local_mode_404() -> None:
    from splitsmith.ui.server import create_app

    # Unbound local app: no hosted env, so state.resolve_share_token is None
    # and the whole share surface is a uniform 404.
    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get(_share_url("anything", "match/shooters"))
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


# -- whitelist regex lock ------------------------------------------------


@pytest.mark.parametrize(
    "rest",
    [
        "match/shooters",
        "shooters/anna/project",
        "shooters/anna/stages/1/coach",
        "shooters/s_ab12/stages/12/coach",
        "shooters/anna/videos/stream",
    ],
)
def test_share_path_re_accepts(rest: str) -> None:
    from splitsmith.ui.server import _SHARE_PATH_RE

    assert _SHARE_PATH_RE.fullmatch(rest) is not None


@pytest.mark.parametrize(
    "rest",
    [
        "",
        "match",
        "match/shooters/",
        "shooters//project",
        "shooters/anna/videos",
        "shooters/anna/project/extra",
        "shooters/a/stages/x/coach",
        "SHOOTERS/a/project",
        "shooters/a/stages/1/coach/distributions",
        "shooters/a/b/project",
        "shooters/anna/stages/1/coach/reclassify",
        "me",
        "match/shares",
    ],
)
def test_share_path_re_rejects(rest: str) -> None:
    from splitsmith.ui.server import _SHARE_PATH_RE

    assert _SHARE_PATH_RE.fullmatch(rest) is None
