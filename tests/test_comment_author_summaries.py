"""GET /api/match/comment-authors -- owner-only author detail (#867).

The name history is the impersonation signal: an account that renamed
itself to match another commenter shows two handles under one code,
which no single comment can reveal.

Owner-only by construction, not by a check in the handler: the route is
absent from _SHARE_PATH_RE, so an anonymous caller gets the same uniform
404 the share surface returns for anything it does not admit.

Copies its preamble from tests/test_comments_signed_in.py verbatim, per
this repo's convention that each comment test file carries its own
self-contained fixture set rather than importing another file's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.db import SESSION_COOKIE_NAME, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject
from splitsmith.ui.comments import AUTHOR_KEY_HEADER
from tests.hosted_helpers import login, seed_match

KEY = "c" * 64

MID = "signed-in-match-1"
SLUG = "alice"
STAGE = 3


def _post(client, token, key=KEY, **headers):
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: key, **headers},
    )


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the share
    routes need to resolve, as the user identified by ``user_email``
    (call after login). Mirrors tests/test_comments_api.py."""

    async def _seed() -> None:
        engine = create_engine(db_url)
        sf = sessionmaker(engine)
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
    """Mint a share token directly through ``ShareTokenStore``. Mirrors
    tests/test_comments_api.py."""
    from splitsmith.db.share_tokens import ShareTokenStore

    async def _mint() -> str:
        engine = create_engine(db_url)
        sf = sessionmaker(engine)
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
    session cookie so the client is anonymous again. Mirrors
    tests/test_comments_api.py's fixture of the same name."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    client.cookies.clear()


@pytest.fixture
def comment_token_client(
    hosted_env: str, hosted_app, _seeded_match: None
) -> Iterator[tuple[TestClient, str]]:
    client, _ = hosted_app
    token = _mint_share_token(hosted_env, "owner@example.com", MID, scope="comment")
    yield client, token


@pytest.fixture
def owner_client(hosted_env: str, hosted_app, _seeded_match: None) -> Iterator[TestClient]:
    """A second TestClient sharing the same app, authenticated as the
    match owner. A separate client (not the shared one comment_token_client
    posts through) so the owner's session cookie never lands on an
    anonymous request in the same test."""
    client, sender = hosted_app
    owner = TestClient(client.app, follow_redirects=False)
    login(owner, sender, "owner@example.com")
    yield owner


def test_summaries_group_by_author_code(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    _post(client, token, key="a" * 64)
    _post(client, token, key="a" * 64)
    _post(client, token, key="b" * 64)

    resp = owner_client.get(f"/api/matches/{MID}/match/comment-authors")

    assert resp.status_code == 200
    authors = resp.json()["authors"]
    assert len(authors) == 2
    assert sorted(a["comment_count"] for a in authors) == [1, 2]


def test_every_handle_a_code_posted_under_is_listed(
    hosted_env: str, hosted_app, comment_token_client, owner_client
) -> None:
    """One account, two names, one code. This is the whole point."""
    client, sender = hosted_app
    share_client, token = comment_token_client
    login(client, sender, "renamer@example.com")
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}
    client.cookies.clear()

    client.patch("/api/me", json={"display_name": "Anders Berg"}, headers=headers)
    _post(share_client, token, **headers)
    client.patch("/api/me", json={"display_name": "Bertil Lund"}, headers=headers)
    _post(share_client, token, **headers)

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    account = [a for a in authors if a["author_kind"] == "account"]
    assert len(account) == 1
    assert sorted(account[0]["handles"]) == ["Anders Berg", "Bertil Lund"]
    assert account[0]["comment_count"] == 2


def test_soft_deleted_comments_are_excluded(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, key="a" * 64).json()
    _post(client, token, key="a" * 64)
    owner_client.delete(f"/api/matches/{MID}/shooters/alice/stages/3/comments/{created['id']}")

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    assert [a["comment_count"] for a in authors] == [1]


def test_an_anonymous_caller_gets_a_404(comment_token_client) -> None:
    client, token = comment_token_client

    resp = client.get(f"/api/share/{token}/match/comment-authors")

    assert resp.status_code == 404


def test_first_comment_at_is_the_earliest(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    _post(client, token, key="a" * 64)

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    assert authors[0]["first_comment_at"][:19] == first["created_at"][:19]


def test_handles_are_ordered_oldest_first(
    hosted_env: str, hosted_app, comment_token_client, owner_client
) -> None:
    """The requirement is stronger than 'the two names appear': the
    order they were posted under must be preserved, not incidental to
    dict insertion order or sorted() elsewhere."""
    client, sender = hosted_app
    share_client, token = comment_token_client
    login(client, sender, "renamer2@example.com")
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}
    client.cookies.clear()

    client.patch("/api/me", json={"display_name": "Zed Zorro"}, headers=headers)
    _post(share_client, token, **headers)
    client.patch("/api/me", json={"display_name": "Anna Astrid"}, headers=headers)
    _post(share_client, token, **headers)
    client.patch("/api/me", json={"display_name": "Mona Munk"}, headers=headers)
    _post(share_client, token, **headers)

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]
    account = [a for a in authors if a["author_kind"] == "account"][0]

    assert account["handles"] == ["Zed Zorro", "Anna Astrid", "Mona Munk"]
