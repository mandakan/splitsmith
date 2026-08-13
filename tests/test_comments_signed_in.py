"""A signed-in visitor comments under their account name.

Mirrors tests/test_comments_api.py's seeding/minting helpers rather than
importing them - same convention test_comments_moderation.py follows, so
this file stays a self-contained fixture set rather than reaching into
another test module's fixture graph.

The rule under test: a resolved session on a share request grants
nothing beyond a display name. It must not touch the tenant, the write
allowlist, or the scope gate - those are Task 6/Task 8's settled
invariants, and the three ``test_a_session_does_not_*`` tests below exist
to catch a future change that lets a session start authorizing anything.
A fourth containment test, ``test_a_scope_limited_desktop_token_cannot_
name_a_comment``, catches the same class of bug from a different
credential: a bearer token confined to ``/api/sync/*`` must not be able
to pick who a comment posts as either, even though the share token still
authorizes the write itself.
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


def _mint_desktop_token(db_url: str, user_email: str, *, scope: str = "sync") -> str:
    """Mint a desktop bearer token directly through ``DesktopTokenStore``,
    for the fix-round-1 finding: a sync-scoped token must not be able to
    name a comment, even on a comment-scoped share link. Requires the
    user row to already exist (call after ``login``)."""
    from splitsmith.db.desktop_tokens import DesktopTokenStore

    async def _mint() -> str:
        engine = create_engine(db_url)
        sf = sessionmaker(engine)
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = DesktopTokenStore(sf, user_id=user_id)
        _record, raw = await store.create("test device", scope=scope)
        return raw

    return asyncio.run(_mint())


def _set_display_name(db_url: str, email: str, display_name: str | None) -> None:
    """Set ``users.display_name`` directly, bypassing PATCH /api/me.

    Kept after #867 for exactly one purpose: seeding states the route
    refuses to produce. ``"   "`` is the important one -- the route
    normalizes a blank name to ``None``, so a whitespace-only column
    value can only be reached by writing the row, and the fallback guard
    it exercises (a non-None string that is blank after stripping) has no
    other way to be tested. Any test asserting the *reachable* account
    branch must go through the route instead; see
    ``test_a_signed_in_visitor_can_set_a_name_and_comment_under_it``.
    """

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


@pytest.fixture
def whitespace_signed_in_headers(hosted_env: str, hosted_app, _seeded_match: None) -> dict[str, str]:
    """A valid session for a user whose display_name is set but is
    whitespace-only ("   "). Distinct from nameless_signed_in_headers
    (display_name=None): None fails the isinstance(str) check on its
    own, so it can't observe the separate .strip() truthiness guard.
    Only a non-None, blank-after-stripping string can."""
    client, sender = hosted_app
    return _session_headers(hosted_env, client, sender, "whitespace@example.com", "   ")


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


def test_signed_in_visitor_with_a_blank_display_name_falls_back_to_a_handle(
    comment_token_client, whitespace_signed_in_headers
) -> None:
    """display_name="   " is a non-None string, so it passes an
    isinstance(str) check on its own - only the .strip() truthiness
    check catches it. Without that check this account would publish a
    comment signed with an empty string: attributed to nobody, which is
    worse than a pseudonym."""
    client, token = comment_token_client
    created = _post(client, token, **whitespace_signed_in_headers).json()
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


def test_a_scope_limited_desktop_token_cannot_name_a_comment(
    hosted_env: str, hosted_app, comment_token_client
) -> None:
    """Fix round 1, Finding 1. ``state.auth`` also resolves the desktop
    bearer backend, and a sync-scoped token is confined to /api/sync/* by
    _auth_gate - a confinement that gate never enforces on /api/share/,
    since it hands off before consulting a session at all. The share
    token still authorizes the write (this is not a wider allowlist); the
    bug this guards against is a credential that isn't even allowed onto
    this surface getting to pick whose name lands on the comment."""
    client, sender = hosted_app
    _, token = comment_token_client
    login(client, sender, "victim@example.com")
    client.cookies.clear()
    _set_display_name(hosted_env, "victim@example.com", "Victim Person")
    bearer = _mint_desktop_token(hosted_env, "victim@example.com", scope="sync")

    created = _post(client, token, Authorization=f"Bearer {bearer}").json()
    assert created["author_kind"] == "handle"
    assert created["author_handle"] != "Victim Person"


def test_a_session_is_resolved_only_on_writes(
    hosted_env: str, hosted_app, comment_token_client, signed_in_headers, monkeypatch
) -> None:
    """Fix round 1, Finding 2. The brief says the lookup must happen only
    on writes, but nothing failed if a refactor moved it above the
    needs_write_scope branch - which would land a session lookup on
    every anonymous card/list fetch. Count calls to authenticate_request
    directly: a GET must make zero, a POST exactly one."""
    client, token = comment_token_client
    state = client.app.state.splitsmith_state
    original = state.auth.authenticate_request
    calls: list[None] = []

    async def _counting(request):
        calls.append(None)
        return await original(request)

    monkeypatch.setattr(state.auth, "authenticate_request", _counting)

    get_resp = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments")
    assert get_resp.status_code == 200
    assert len(calls) == 0

    _post(client, token, **signed_in_headers)
    assert len(calls) == 1


def test_a_signed_in_visitor_can_set_a_name_and_comment_under_it(
    hosted_env: str, hosted_app, comment_token_client
) -> None:
    """The reachability proof for #867.

    Nothing here writes users.display_name directly. The visitor signs
    in, sets a name through the same route the /account page calls, and
    posts through a comment-scoped share link. Before #867 there was no
    such route, so this branch could not be reached by any sequence of
    requests a real user could make.
    """
    client, sender = hosted_app
    login(client, sender, "reachable@example.com")
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    assert secret is not None

    resp = client.patch("/api/me", json={"display_name": "Anders Berg"})
    assert resp.status_code == 200

    client.cookies.clear()
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}
    share_client, token = comment_token_client
    created = _post(share_client, token, **headers).json()

    assert created["author_kind"] == "account"
    assert created["author_handle"] == "Anders Berg"
    assert len(created["author_code"]) == 6


def test_two_accounts_with_the_same_name_get_different_codes(
    hosted_env: str, hosted_app, comment_token_client
) -> None:
    """The disambiguation the code exists for. Two real accounts, one
    name, two codes."""
    client, sender = hosted_app
    share_client, token = comment_token_client
    codes = []
    for email in ("twin-a@example.com", "twin-b@example.com"):
        login(client, sender, email)
        secret = client.cookies.get(SESSION_COOKIE_NAME)
        assert secret is not None
        assert client.patch("/api/me", json={"display_name": "Anders Berg"}).status_code == 200
        client.cookies.clear()
        created = _post(
            share_client,
            token,
            key=email.replace("@", "").ljust(64, "x")[:64],
            **{"Cookie": f"{SESSION_COOKIE_NAME}={secret}"},
        ).json()
        assert created["author_handle"] == "Anders Berg"
        codes.append(created["author_code"])

    assert codes[0] != codes[1]
