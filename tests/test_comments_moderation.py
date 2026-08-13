"""Owner-side moderation: the release condition for anonymous writes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.comment_identity import hash_author_key
from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject
from splitsmith.ui.comments import AUTHOR_KEY_HEADER
from tests.hosted_helpers import login, seed_match

MID = "moderation-match-1"
SLUG = "alice"
STAGE = 3
KEY_A = "a" * 64
KEY_B = "b" * 64


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the comment
    routes need to resolve. Mirrors tests/test_comments_api.py."""

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


class _AliasedClient:
    """Resolves bare ``/api/...`` paths against the
    ``/api/matches/{match_id}/...`` alias prefix that
    ``_match_id_alias`` requires in hosted mode to set ``current_match_id``.

    Lets this file's tests read the way the brief specifies (bare paths,
    matching ``tests/test_comments_api.py``'s ``owner_client`` style) while
    still exercising the real routes, which 409 (``no_project``) on a bare
    path in hosted mode with nothing bound. Anonymous ``/api/share/...``
    paths are passed through unchanged - they carry their own match id via
    the token."""

    def __init__(self, client: TestClient, match_id: str) -> None:
        self._client = client
        self._match_id = match_id

    @property
    def app(self) -> object:
        return self._client.app

    def _alias(self, url: str) -> str:
        if url.startswith("/api/share/"):
            return url
        assert url.startswith("/api/"), url
        return f"/api/matches/{self._match_id}/{url[len('/api/') :]}"

    def get(self, url: str, **kw: object) -> object:
        return self._client.get(self._alias(url), **kw)

    def post(self, url: str, **kw: object) -> object:
        return self._client.post(self._alias(url), **kw)

    def delete(self, url: str, **kw: object) -> object:
        return self._client.delete(self._alias(url), **kw)


@pytest.fixture
def owner_client(hosted_env: str, hosted_app) -> Iterator[_AliasedClient]:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    yield _AliasedClient(client, MID)


def _anon(owner_client: _AliasedClient) -> TestClient:
    """A cookie-free client on the same app, for posting through a share
    link without disturbing the owner's session."""
    return TestClient(owner_client.app, follow_redirects=False)


def _token_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _mint_comment_link(owner_client: _AliasedClient) -> tuple[str, str]:
    """Mint a comment-scoped link as the owner. Returns (share_token_id, token)."""
    created = owner_client.post("/api/match/shares", json={"scope": "comment"}).json()
    return created["id"], _token_from_url(created["url"])


def _post_comment(owner_client: _AliasedClient, token: str, *, key: str) -> str:
    """Post one comment through ``token`` as ``key``. Returns the comment id."""
    anon = _anon(owner_client)
    resp = anon.post(
        f"/api/share/{token}/shooters/{SLUG}/stages/{STAGE}/comments",
        json={"body": "reload looks early", "anchor_t": 4.32},
        headers={AUTHOR_KEY_HEADER: key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
def seeded_comment(owner_client: _AliasedClient) -> str:
    _, token = _mint_comment_link(owner_client)
    return _post_comment(owner_client, token, key=KEY_A)


@pytest.fixture
def two_links_two_comments(owner_client: _AliasedClient) -> tuple[str, str]:
    """Two comment-scoped links, one comment posted through each.
    Returns the two links' share_token_ids."""
    token_id_a, token_a = _mint_comment_link(owner_client)
    token_id_b, token_b = _mint_comment_link(owner_client)
    _post_comment(owner_client, token_a, key=KEY_A)
    _post_comment(owner_client, token_b, key=KEY_B)
    return token_id_a, token_id_b


@pytest.fixture
def two_authors_two_comments(owner_client: _AliasedClient) -> tuple[str, str]:
    """One comment-scoped link, two comments posted by two different
    authors. Returns the two authors' hashed keys."""
    _, token = _mint_comment_link(owner_client)
    _post_comment(owner_client, token, key=KEY_A)
    _post_comment(owner_client, token, key=KEY_B)
    return hash_author_key(KEY_A), hash_author_key(KEY_B)


@pytest.fixture
def comment_token_client(owner_client: _AliasedClient) -> tuple[TestClient, str]:
    _, token = _mint_comment_link(owner_client)
    return _anon(owner_client), token


def test_mint_defaults_to_read_scope(owner_client) -> None:
    created = owner_client.post("/api/match/shares", json={}).json()
    assert created["scope"] == "read"


def test_mint_can_request_the_comment_scope(owner_client) -> None:
    created = owner_client.post("/api/match/shares", json={"scope": "comment"}).json()
    assert created["scope"] == "comment"


def test_mint_rejects_an_unknown_scope(owner_client) -> None:
    assert owner_client.post("/api/match/shares", json={"scope": "admin"}).status_code == 422


def test_owner_sees_the_thread_with_moderation_fields(owner_client, seeded_comment) -> None:
    body = owner_client.get(f"/api/shooters/{SLUG}/stages/{STAGE}/comments").json()
    comment = body["comments"][0]
    assert comment["share_token_id"]
    assert comment["author_key_hash"]


def test_owner_can_delete_any_comment(owner_client, seeded_comment) -> None:
    resp = owner_client.delete(f"/api/shooters/{SLUG}/stages/{STAGE}/comments/{seeded_comment}")
    assert resp.status_code == 204
    assert owner_client.get(f"/api/shooters/{SLUG}/stages/{STAGE}/comments").json()["comments"] == []


def test_bulk_delete_by_share_token(owner_client, two_links_two_comments) -> None:
    token_id, _ = two_links_two_comments
    resp = owner_client.delete(f"/api/match/comments?share_token_id={token_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    assert len(owner_client.get(f"/api/shooters/{SLUG}/stages/{STAGE}/comments").json()["comments"]) == 1


def test_bulk_delete_by_author_key_hash(owner_client, two_authors_two_comments) -> None:
    key_hash, _ = two_authors_two_comments
    resp = owner_client.delete(f"/api/match/comments?author_key_hash={key_hash}")
    assert resp.json() == {"deleted": 1}


def test_bulk_delete_requires_exactly_one_selector(owner_client) -> None:
    assert owner_client.delete("/api/match/comments").status_code == 422
    assert owner_client.delete("/api/match/comments?share_token_id=a&author_key_hash=b").status_code == 422


def test_a_share_request_cannot_reach_the_bulk_delete(comment_token_client) -> None:
    """Not in either allowlist, so it is the uniform 404 - not a 403."""
    client, token = comment_token_client
    resp = client.delete(f"/api/share/{token}/match/comments?share_token_id=x")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}
