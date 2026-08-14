"""PATCH /api/me -- the route that makes users.display_name writable (#867).

Before this route existed the column was NULL for every real account, so
#866's ``author_kind="account"`` branch was unreachable in production.
The end-to-end proof of reachability lives in
tests/test_comments_signed_in.py; this file covers the route itself.

Fixture conventions mirror the other hosted API tests: ``hosted_app``
yields a (TestClient, email sender) pair and ``login`` drives the
magic-link flow. There is no shared ``client`` fixture for local mode in
this repo (verified against tests/conftest.py) -- local-mode coverage
below follows tests/test_auth_routes.py's
``test_auth_routes_404_in_local_mode`` pattern instead: build the app
directly with ``SPLITSMITH_MODE`` unset.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from splitsmith.display_name import MAX_DISPLAY_NAME_LEN
from tests.hosted_helpers import login


def test_patch_sets_the_display_name(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "namer@example.com")

    resp = client.patch("/api/me", json={"display_name": "Anders Berg"})

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Anders Berg"
    assert client.get("/api/me").json()["display_name"] == "Anders Berg"


def test_patch_normalizes_before_storing(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "messy@example.com")

    resp = client.patch("/api/me", json={"display_name": "  Anders    Berg  "})

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Anders Berg"


def test_null_clears_the_display_name(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "clearer@example.com")
    client.patch("/api/me", json={"display_name": "Anders Berg"})

    resp = client.patch("/api/me", json={"display_name": None})

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


def test_blank_stores_null_not_empty_string(hosted_app) -> None:
    """The #866 fallback invariant, enforced at the write boundary: an
    account with a blank name must never publish an empty author."""
    client, sender = hosted_app
    login(client, sender, "blank@example.com")

    resp = client.patch("/api/me", json={"display_name": "   "})

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


@pytest.mark.parametrize(
    "bad",
    ["a" * (MAX_DISPLAY_NAME_LEN + 1), "Anders\nBerg", "Anders\x00Berg"],
)
def test_invalid_names_are_422(hosted_app, bad: str) -> None:
    client, sender = hosted_app
    login(client, sender, "invalid@example.com")

    resp = client.patch("/api/me", json={"display_name": bad})

    assert resp.status_code == 422


def test_a_rejected_name_is_not_persisted(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "rejected@example.com")
    client.patch("/api/me", json={"display_name": "Anders Berg"})

    client.patch("/api/me", json={"display_name": "a" * (MAX_DISPLAY_NAME_LEN + 1)})

    assert client.get("/api/me").json()["display_name"] == "Anders Berg"


def test_a_missing_field_is_422(hosted_app) -> None:
    """display_name is required-but-nullable, so an empty body cannot be
    read as 'clear it' by accident."""
    client, sender = hosted_app
    login(client, sender, "empty@example.com")

    assert client.patch("/api/me", json={}).status_code == 422


def test_anonymous_is_401(hosted_app) -> None:
    client, _ = hosted_app
    client.cookies.clear()

    assert client.patch("/api/me", json={"display_name": "Nobody"}).status_code == 401


def test_local_mode_404s(monkeypatch: pytest.MonkeyPatch) -> None:
    """LoopbackAuth's sentinel user has no row to write. The magic-link
    routes 404 in local mode for the same reason."""
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        resp = client.patch("/api/me", json={"display_name": "Local"})

    assert resp.status_code == 404


def test_a_sync_scoped_desktop_token_cannot_set_a_name(hosted_env: str, hosted_app) -> None:
    """A sync-scoped token is confined to /api/sync/* by _auth_gate's scope
    check (server.py, around line 7395): any ``token_scope`` other than
    None/"full" gets a blanket 403 on any path that is not under
    ``/api/sync/`` or ``/api/device/session``, before the request ever
    reaches ``patch_me``. This differs from #866's comment-route
    containment, which hands off to ``/api/share/*`` *before* the scope
    check runs and instead enforces containment inside the handler
    (yielding a fallback to ``author_kind="handle"``, not an HTTP error).
    ``/api/me`` has no such carve-out, so confirmed empirically: the
    response here is 403, not 401/404."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import User, create_engine, sessionmaker
    from splitsmith.db.desktop_tokens import DesktopTokenStore

    client, sender = hosted_app
    login(client, sender, "tokened@example.com")
    client.cookies.clear()

    async def _mint() -> str:
        engine = create_engine(hosted_env)
        sf = sessionmaker(engine)
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == "tokened@example.com"))).scalar_one()
        store = DesktopTokenStore(sf, user_id=row.id)
        _record, raw = await store.create("test device", scope="sync")
        return raw

    raw = asyncio.run(_mint())

    resp = client.patch(
        "/api/me",
        json={"display_name": "Impostor"},
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 403
