"""The scope gate: a sync-scoped desktop token reaches /api/sync/* and
nothing else (#719).

This is the security-critical seam of the whole change. It is one
``if`` in ``_auth_gate``, and these tests are the only thing standing
behind it, so run the mutation drill before trusting them: delete the
gate, watch every test in this file that asserts a 403 go red, restore.

Tokens are seeded directly into the DB with an explicit scope rather
than driven through the device flow, so the gate is tested independently
of the flow that produces its input.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from splitsmith.db import DesktopTokenRow, User, create_engine, sessionmaker
from splitsmith.db.workers import _mint
from tests.hosted_helpers import _CapturingSender, login


def _seed_token(db_url: str, email: str, *, scope: str) -> str:
    """Insert a desktop token with an explicit scope; return the raw value."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)
    plain, hashed = _mint()

    async def _insert() -> None:
        async with sf() as s:
            user = (await s.execute(select(User).where(User.email == email))).scalar_one()
            s.add(
                DesktopTokenRow(
                    user_id=user.id,
                    name="mac studio",
                    token_hash=hashed,
                    scope=scope,
                )
            )
            await s.commit()

    asyncio.run(_insert())
    return plain


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sync_token_reaches_the_sync_surface(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    # login() left a session cookie on this client; without clearing it,
    # every request below would authenticate via the (unrestricted)
    # cookie instead of the bearer token under test, since CompositeAuth
    # tries the cookie backend first (#719).
    client.cookies.clear()

    resp = client.post(
        "/api/sync/matches",
        json={"match_id": "m-scope", "name": "Scope Test"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text


def test_sync_token_is_403_on_the_match_surface(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()  # isolate the bearer token; see comment above

    resp = client.get("/api/me/recent-projects", headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "token scope"


def test_sync_token_is_403_on_desktop_token_management(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """A sync token must not be able to mint itself a wider one."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()  # isolate the bearer token; see comment above

    resp = client.post("/api/me/desktop-tokens", json={"name": "wider"}, headers=_auth(token))
    assert resp.status_code == 403, resp.text


def test_sync_token_is_403_on_api_me(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """Recorded consequence: the local install learns its account from the
    device-flow poll response, not from a live /api/me lookup."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()  # isolate the bearer token; see comment above

    assert client.get("/api/me", headers=_auth(token)).status_code == 403


def test_sync_token_may_delete_its_own_session(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """The single exception - it is what lets the local UI sign out
    without holding a cookie."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()  # isolate the bearer token; see comment above

    assert client.delete("/api/device/session", headers=_auth(token)).status_code == 200


def test_full_token_is_unaffected(hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str) -> None:
    """An install in the field holding a pasted token must not break."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="full")
    client.cookies.clear()  # isolate the bearer token; see comment above

    assert client.get("/api/me", headers=_auth(token)).status_code == 200


def test_session_cookie_is_unaffected(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """MagicLinkAuth leaves token_scope None -- unrestricted."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.get("/api/me").status_code == 200


def test_sync_token_cannot_reach_a_sync_lookalike_prefix(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """``startswith("/api/sync/")`` with the trailing slash: a route named
    ``/api/syncthing`` must not slip through the gate."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()  # isolate the bearer token; see comment above

    # No such route exists; the point is that the gate answers first.
    resp = client.get("/api/syncthing/whatever", headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "token scope"


def test_unrecognized_scope_is_denied_not_allowed(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """The gate is an allowlist of {None, "full"}, not a denylist of
    {"sync"} - an invented or mistyped scope value must fail closed."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="readonly")
    client.cookies.clear()  # isolate the bearer token; see comment above

    resp = client.get("/api/me", headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "token scope"


def test_sync_token_reads_whoami_but_still_not_me(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """#877's route and the #719 boundary it must not breach, in one test.

    The desktop needs to refresh the account label it caches at link
    time, and cannot read /api/me to do it. The fix was a route on the
    surface the sync scope already reaches -- NOT a wider scope. If a
    future change makes the second assertion pass, the containment #719
    established is gone and the first assertion is no longer evidence of
    anything.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()

    assert client.get("/api/sync/whoami", headers=_auth(token)).status_code == 200
    assert client.get("/api/me", headers=_auth(token)).status_code == 403
