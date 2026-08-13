"""Anonymous comment endpoints on the share surface.

Adversarial cases first. The happy path either works or obviously does
not; the containment properties fail silently.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from splitsmith.ui.comments import AUTHOR_KEY_HEADER, BODY_MAX_CHARS
from tests.hosted_helpers import login, seed_match

NOT_FOUND = {"detail": "not found"}
KEY = "a" * 64

MID = "comments-match-1"
SLUG = "alice"
STAGE = 3


def _post(client, token, *, key=KEY, **body):
    payload = {"body": "reload looks early", "anchor_t": 4.32, **body}
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json=payload,
        headers={AUTHOR_KEY_HEADER: key},
    )


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the share
    routes need to resolve, as the user identified by ``user_email``
    (call after login). Mirrors ``tests/test_share_routes.py``."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith import match_model
    from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
    from splitsmith.match_project import MatchProject

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
            stages=[match_model.MatchStageDefinition(stage_number=STAGE, stage_name="Stage 3")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Alice")
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


def _mint_share_token(db_url: str, user_email: str, match_id: str, *, scope: str) -> str:
    """Mint a share token directly through ``ShareTokenStore``.

    There is no HTTP route yet that lets an owner choose a scope other
    than "read" when minting a link - comment-scoped link minting is
    Task 8's job. This task only needs a comment-scoped token to exist
    so the write surface can be exercised.
    """
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import User, create_engine, sessionmaker
    from splitsmith.db.share_tokens import ShareTokenStore

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _mint() -> str:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ShareTokenStore(sf, user_id=user_id)
        created = await store.create(match_id, scope=scope)
        return created.token

    return asyncio.run(_mint())


@pytest.fixture
def _seeded_match(hosted_env: str, hosted_app) -> None:
    """Login as owner, seed a match + shooter + state docs, then drop the
    session cookie so the client is anonymous again.

    Function-scoped and depended on by both ``read_token_client`` and
    ``comment_token_client`` - pytest caches a fixture's result per test,
    so a test that asks for both (``test_read_scoped_token_can_read_the_
    thread``) seeds the match exactly once rather than colliding on the
    same (user_id, match_id) unique constraint.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    client.cookies.clear()


@pytest.fixture
def read_token_client(hosted_env: str, hosted_app, _seeded_match: None) -> Iterator[tuple[TestClient, str]]:
    client, _ = hosted_app
    token = _mint_share_token(hosted_env, "owner@example.com", MID, scope="read")
    yield client, token


@pytest.fixture
def comment_token_client(
    hosted_env: str, hosted_app, _seeded_match: None
) -> Iterator[tuple[TestClient, str]]:
    client, _ = hosted_app
    token = _mint_share_token(hosted_env, "owner@example.com", MID, scope="comment")
    yield client, token


@pytest.fixture
def owner_client(hosted_env: str, hosted_app) -> Iterator[TestClient]:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    yield client


@pytest.fixture
def other_user_id(hosted_env: str, hosted_app) -> str:
    """A real, distinct user id - so a crafted ``author_user_id`` in a
    POST body names an actual row rather than an obviously-fake string."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import User, create_engine, sessionmaker

    client, sender = hosted_app
    login(client, sender, "other@example.com")
    client.cookies.clear()

    engine = create_engine(hosted_env)
    sf = sessionmaker(engine)

    async def _lookup() -> str:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == "other@example.com"))).scalar_one()
            return row.id

    return asyncio.run(_lookup())


# --- containment ---------------------------------------------------------


def test_post_through_a_read_scoped_token_is_the_uniform_404(read_token_client) -> None:
    client, token = read_token_client
    resp = _post(client, token)
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_read_token_404_is_identical_to_an_unknown_token_404(read_token_client) -> None:
    client, token = read_token_client
    denied = _post(client, token)
    unknown = _post(client, "not-a-real-token")
    assert (denied.status_code, denied.json()) == (unknown.status_code, unknown.json())


def test_comment_token_cannot_reach_a_non_allowlisted_write_path(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/audit/accept",
        json={},
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 404


def test_comment_token_cannot_use_an_unlisted_method(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.put(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "x", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 404


def test_body_cannot_set_owner_or_author_fields(comment_token_client, other_user_id) -> None:
    """A crafted POST must not choose its own name or move the row into
    another tenant."""
    client, token = comment_token_client
    resp = _post(
        client,
        token,
        author_handle="Mathias Axell",
        author_user_id=other_user_id,
        user_id=other_user_id,
        match_id="some-other-match",
        author_kind="account",
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["author_handle"] != "Mathias Axell"
    assert created["author_kind"] == "handle"


def test_list_never_exposes_author_key_hash_or_share_token(comment_token_client) -> None:
    client, token = comment_token_client
    _post(client, token)
    body = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()
    assert body["comments"]
    for comment in body["comments"]:
        assert "author_key_hash" not in comment
        assert "share_token_id" not in comment
        assert "author_user_id" not in comment


# --- read scope sees the thread but cannot join it -----------------------


def test_read_scoped_token_can_read_the_thread(read_token_client, comment_token_client) -> None:
    writer, write_token = comment_token_client
    _post(writer, write_token)
    reader, read_token = read_token_client
    resp = reader.get(f"/api/share/{read_token}/shooters/alice/stages/3/comments")
    assert resp.status_code == 200
    assert len(resp.json()["comments"]) == 1


# --- happy path + validation --------------------------------------------


def test_post_then_list_round_trips(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token).json()
    listed = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()
    assert [c["id"] for c in listed["comments"]] == [created["id"]]
    assert listed["comments"][0]["body"] == "reload looks early"


def test_handle_is_stable_across_two_posts_from_one_key(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, body="one").json()
    second = _post(client, token, body="two").json()
    assert first["author_handle"] == second["author_handle"]


def test_a_different_key_gets_a_different_handle(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    second = _post(client, token, key="b" * 64).json()
    assert first["author_handle"] != second["author_handle"]


def test_shot_anchor_keeps_both_fields(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, anchor_kind="shot", anchor_shot_id="cand-7").json()
    assert created["anchor_kind"] == "shot"
    assert created["anchor_shot_id"] == "cand-7"
    assert created["anchor_t"] == pytest.approx(4.32)


def test_shot_kind_without_a_shot_id_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    resp = _post(client, token, anchor_kind="shot", anchor_shot_id=None)
    assert resp.status_code == 422


def test_empty_body_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    assert _post(client, token, body="   ").status_code == 422


def test_oversized_body_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    assert _post(client, token, body="x" * (BODY_MAX_CHARS + 1)).status_code == 422


def test_missing_author_key_header_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "hi", "anchor_t": 1.0},
    )
    assert resp.status_code == 422


def test_anchor_t_is_clamped_and_rounded(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, anchor_t=9999.999).json()
    assert created["anchor_t"] == pytest.approx(3600.0)
    created = _post(client, token, anchor_t=1.23456).json()
    assert created["anchor_t"] == pytest.approx(1.23)


# --- self delete ---------------------------------------------------------


def test_author_can_delete_their_own_comment(comment_token_client) -> None:
    client, token = comment_token_client
    cid = _post(client, token).json()["id"]
    resp = client.delete(
        f"/api/share/{token}/shooters/alice/stages/3/comments/{cid}",
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 204
    assert client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()["comments"] == []


def test_another_key_cannot_delete_it(comment_token_client) -> None:
    client, token = comment_token_client
    cid = _post(client, token).json()["id"]
    resp = client.delete(
        f"/api/share/{token}/shooters/alice/stages/3/comments/{cid}",
        headers={AUTHOR_KEY_HEADER: "b" * 64},
    )
    assert resp.status_code == 404


def test_mine_is_true_only_for_the_posting_key(comment_token_client) -> None:
    client, token = comment_token_client
    _post(client, token)
    mine = client.get(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        headers={AUTHOR_KEY_HEADER: KEY},
    ).json()["comments"][0]
    theirs = client.get(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        headers={AUTHOR_KEY_HEADER: "b" * 64},
    ).json()["comments"][0]
    anonymous = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()["comments"][0]
    assert mine["mine"] is True
    assert theirs["mine"] is False
    assert anonymous["mine"] is False


# --- owner-side delete branch (shared route, not a new endpoint) --------


def test_owner_can_delete_a_comment_posted_through_their_own_link(
    hosted_env: str, owner_client: TestClient
) -> None:
    """The DELETE route is shared: the owner branch (``current_share_request``
    is False) reaches ``delete_as_owner``. No new owner-only route is added
    here - moderation UI is Task 8.

    A second ``TestClient`` bound to the same app posts anonymously so
    ``owner_client``'s session cookie is never disturbed."""
    token = _mint_share_token(hosted_env, "owner@example.com", MID, scope="comment")
    anon = TestClient(owner_client.app, follow_redirects=False)
    cid = _post(anon, token).json()["id"]

    resp = owner_client.delete(f"/api/matches/{MID}/shooters/{SLUG}/stages/{STAGE}/comments/{cid}")
    assert resp.status_code == 204
