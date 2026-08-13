"""A signed-in visitor comments under their account name.

Mirrors tests/test_comments_api.py's seeding/minting helpers rather than
importing them - same convention test_comments_moderation.py follows, so
this file stays a self-contained fixture set rather than reaching into
another test module's fixture graph.

The rule under test: a resolved session on a share request grants
nothing beyond a display name. It must not touch the tenant, the write
allowlist, or the scope gate - those are Task 6/Task 8's settled
invariants, and the four ``test_a_session_does_not_*`` tests below exist
to catch a future change that lets a session start authorizing anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select
from sqlalchemy import update as _update

from splitsmith import match_model
from splitsmith.db import SESSION_COOKIE_NAME, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject
from splitsmith.ui.comments import AUTHOR_KEY_HEADER
from tests.hosted_helpers import login, seed_match

KEY = "c" * 64

MID = "signed-in-match-1"
SLUG = "alice"
STAGE = 3


def _post(client, token, **headers):
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: KEY, **headers},
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


def _set_display_name(db_url: str, email: str, display_name: str | None) -> None:
    """Set ``users.display_name`` directly. There is no route that lets
    an account set its own display name yet, so tests reach the row
    the same way ``seed_match`` reaches ``matches``."""

    async def _update_row() -> None:
        engine = create_engine(db_url)
        sf = sessionmaker(engine)
        async with sf() as s:
            await s.execute(_update(User).where(User.email == email).values(display_name=display_name))
            await s.commit()

    asyncio.run(_update_row())


def _session_headers(
    hosted_env: str, client: TestClient, sender, email: str, display_name: str | None
) -> dict[str, str]:
    """Log a fresh (non-owner) user in on the shared client, optionally
    give them a display name, and hand back headers carrying the raw
    session cookie - then clear the cookie from the shared client so
    comment_token_client's requests stay anonymous by default. Every
    caller in this file passes the cookie explicitly per-request instead
    of relying on the client's cookie jar."""
    login(client, sender, email)
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    assert secret is not None
    client.cookies.clear()
    if display_name is not None:
        _set_display_name(hosted_env, email, display_name)
    return {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}


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
def owner_client(hosted_env: str, hosted_app, _seeded_match: None) -> Iterator[TestClient]:
    """A second TestClient sharing the same app, authenticated as the
    match owner. A separate client (not the shared one comment_token_client
    posts through) so the owner's session cookie never lands on an
    anonymous request in the same test."""
    client, sender = hosted_app
    owner = TestClient(client.app, follow_redirects=False)
    login(owner, sender, "owner@example.com")
    yield owner


@pytest.fixture
def signed_in_headers(hosted_env: str, hosted_app, _seeded_match: None) -> dict[str, str]:
    """A valid session for a user with display_name="Anders Berg" who is
    not the match owner."""
    client, sender = hosted_app
    return _session_headers(hosted_env, client, sender, "commenter@example.com", "Anders Berg")


@pytest.fixture
def nameless_signed_in_headers(hosted_env: str, hosted_app, _seeded_match: None) -> dict[str, str]:
    """A valid session for a user who never set a display name."""
    client, sender = hosted_app
    return _session_headers(hosted_env, client, sender, "nameless@example.com", None)


# ---------------------------------------------------------------------------


def test_anonymous_visitor_gets_a_generated_handle(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token).json()
    assert created["author_kind"] == "handle"
    assert created["author_handle"].split(" ")[-1].isdigit()


def test_signed_in_visitor_uses_their_display_name(comment_token_client, signed_in_headers) -> None:
    client, token = comment_token_client
    created = _post(client, token, **signed_in_headers).json()
    assert created["author_kind"] == "account"
    assert created["author_handle"] == "Anders Berg"


def test_signed_in_visitor_without_a_display_name_falls_back_to_a_handle(
    comment_token_client, nameless_signed_in_headers
) -> None:
    """display_name is nullable. An account with none must not post as
    an empty string or as their email address."""
    client, token = comment_token_client
    created = _post(client, token, **nameless_signed_in_headers).json()
    assert created["author_kind"] == "handle"
    assert created["author_handle"].split(" ")[-1].isdigit()


def test_a_session_does_not_change_the_tenant(comment_token_client, signed_in_headers, owner_client) -> None:
    """The row must land in the OWNER's tenant, not the commenter's. If
    a session ever started driving current_tenant on a share path, this
    is what would catch it."""
    client, token = comment_token_client
    _post(client, token, **signed_in_headers)
    listed = owner_client.get(f"/api/matches/{MID}/shooters/{SLUG}/stages/{STAGE}/comments")
    assert listed.json()["comments"] and len(listed.json()["comments"]) == 1


def test_a_session_does_not_widen_the_allowlist(comment_token_client, signed_in_headers) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/audit/accept",
        json={},
        headers={AUTHOR_KEY_HEADER: KEY, **signed_in_headers},
    )
    assert resp.status_code == 404


def test_a_session_does_not_bypass_the_scope_gate(read_token_client, signed_in_headers) -> None:
    """Being signed in must not make a read-scoped link postable."""
    client, token = read_token_client
    assert _post(client, token, **signed_in_headers).status_code == 404


def test_an_invalid_session_degrades_to_anonymous(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, Cookie="session=garbage").json()
    assert created["author_kind"] == "handle"


def test_a_malformed_real_session_cookie_degrades_to_anonymous(comment_token_client) -> None:
    """Same as test_an_invalid_session_degrades_to_anonymous but under the
    actual session cookie name, so this exercises resolution actually
    failing to find a session row rather than never looking because the
    cookie name didn't match."""
    client, token = comment_token_client
    created = _post(client, token, Cookie=f"{SESSION_COOKIE_NAME}=garbage-not-a-real-secret").json()
    assert created["author_kind"] == "handle"


def test_authenticate_request_raising_degrades_to_anonymous(
    comment_token_client, signed_in_headers, monkeypatch
) -> None:
    """The auth backend may raise on a garbage cookie (brief's stated
    concern). Force it to and confirm the write still succeeds
    anonymously instead of 500ing."""
    client, token = comment_token_client
    app = client.app
    state = app.state.splitsmith_state

    async def _boom(request):
        raise RuntimeError("simulated auth backend failure")

    monkeypatch.setattr(state.auth, "authenticate_request", _boom)
    resp = _post(client, token, **signed_in_headers)
    assert resp.status_code == 201
    assert resp.json()["author_kind"] == "handle"
