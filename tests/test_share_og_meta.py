"""Meta tags on the share shells. Crawlers do not run JavaScript, so these
must be in the served HTML, not rendered by React."""

from __future__ import annotations

import asyncio
import re

from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from tests.hosted_helpers import login, seed_match

MID = "test-match-meta01"
SLUG = "anna"


def _meta(html: str, prop: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', html)
    return m.group(1) if m else None


def _share_token(client: TestClient) -> str:
    resp = client.post(f"/api/matches/{MID}/match/shares")
    assert resp.status_code == 201
    return resp.json()["url"].rsplit("/", 1)[-1]


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str, match_name: str) -> None:
    """Insert the match + per-shooter project + audit state docs the card
    builders read through (state.match() / state.shooter_project() /
    state.load_audit()). ``seed_match`` alone only inserts the
    ownership-check ``MatchRow`` -- it does not create a state doc, so
    ``state.match()`` 404s without this (same helper Task 6 wrote for the
    PNG routes, extended with a real stage + audited shots so the stage
    card actually resolves instead of falling back)."""
    from splitsmith import match_model
    from splitsmith.match_project import MatchProject, StageEntry

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=match_id,
            name=match_name,
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Anna",
            stages=[StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=8.0)],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)
        audit = {
            "stage_number": 1,
            "stage_name": "Stage 1",
            "shots": [
                {"shot_number": 1, "ms_after_beep": 500},
                {"shot_number": 2, "ms_after_beep": 900},
            ],
        }
        await store.save_audit(match_id, slug, 1, audit, expected_version=0)

    asyncio.run(_seed())


def _rename_match(db_url: str, match_id: str, new_name: str) -> None:
    """Change the match's stored name in place, the way ``seed_match``
    writes one. Any mutation that moves a field the match card displays
    will do -- the name is cheapest."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _rename() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == "owner@example.com"))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        doc, version = await store.load_match(match_id)
        assert doc is not None
        doc["name"] = new_name
        await store.save_match(match_id, doc, expected_version=version)

    asyncio.run(_rename())


def _seed(hosted_env: str, match_name: str = f"Test match {MID}") -> None:
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG, match_name)


def test_match_shell_carries_og_tags(hosted_env, hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}").text
    assert _meta(html, "og:title")
    assert _meta(html, "og:image").startswith("http")
    assert "/og.png?v=" in _meta(html, "og:image")
    assert _meta(html, "og:image:width") == "1200"
    assert _meta(html, "og:image:height") == "630"
    assert _meta(html, "twitter:card") == "summary_large_image"


def test_share_shells_are_noindex(hosted_env, hosted_app) -> None:
    """A share link is unlisted, not public."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()
    assert _meta(client.get(f"/share/{token}").text, "robots") == "noindex"


def test_stage_shell_names_the_stage_and_points_at_the_stage_png(hosted_env, hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}/results/{SLUG}/1").text
    assert "Stage 1" in (_meta(html, "og:title") or "")
    assert f"/og/{SLUG}/1.png?v=" in _meta(html, "og:image")


def test_the_og_image_url_moves_when_the_data_moves(hosted_env, hosted_app) -> None:
    """The freshness mechanism, asserted rather than assumed. If the URL does
    not move, a re-audit writes an object nobody fetches and every crawler
    keeps serving stale numbers from behind a year-long cache."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)

    before = _meta(client.get(f"/share/{token}").text, "og:image")
    _rename_match(hosted_env, MID, "A Different Match Name")
    after = _meta(client.get(f"/share/{token}").text, "og:image")

    assert before != after
    assert before.split("?v=")[1] != after.split("?v=")[1]


def test_revoked_and_unknown_tokens_serve_identical_shells(hosted_env, hosted_app) -> None:
    """The meta must not reveal that a token once existed."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    resp = client.post(f"/api/matches/{MID}/match/shares")
    token = resp.json()["url"].rsplit("/", 1)[-1]
    client.delete(f"/api/matches/{MID}/match/shares/{resp.json()['id']}")
    client.cookies.clear()

    revoked = client.get(f"/share/{token}").text
    unknown = client.get("/share/definitely-not-a-token").text
    assert revoked == unknown


def test_the_shell_still_serves_the_spa_bundle(hosted_env, hosted_app) -> None:
    """Meta injection must not break the app for a real browser."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text
    assert resp.headers["cache-control"] == "no-cache"


def test_the_shell_does_not_inherit_the_viewers_session(hosted_env, hosted_app) -> None:
    """The sub-request must be anonymous. A logged-in viewer of someone
    else's share link must get the same shell an anonymous one gets --
    otherwise the shell's data depends on who is looking at it."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)

    with_session = client.get(f"/share/{token}").text
    client.cookies.clear()
    without_session = client.get(f"/share/{token}").text
    assert with_session == without_session


def test_the_og_meta_sub_request_carries_no_headers(hosted_env, hosted_app, monkeypatch) -> None:
    """Direct proof that ``_fetch_og_meta`` sends the sub-request with an
    empty header set. ``/api/share/`` is *also* exempt from the auth gate
    regardless of cookies (defense in depth), which means
    ``test_the_shell_does_not_inherit_the_viewers_session`` alone would keep
    passing even if this function forwarded ``request.headers`` -- that
    exemption is a second, independent reason the shell output wouldn't
    change. This test pins the actual call site instead of the double-
    protected outcome, so it fails on its own if the empty-headers behavior
    regresses, even if the auth-gate exemption is later removed or
    weakened."""
    import httpx

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)

    captured: list[object] = []
    original_get = httpx.AsyncClient.get

    async def _spy_get(self, url, *args, **kwargs):
        captured.append(kwargs.get("headers"))
        return await original_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", _spy_get)

    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert captured, "the ASGI sub-request was never made"
    assert captured[0] == {}


def test_the_shell_is_entered_exactly_once_per_request(hosted_env, hosted_app, caplog) -> None:
    """Proof there is no recursion through the og-meta sub-request: the
    shell handler logs on entry, and a single top-level GET must produce
    exactly one such log line. An accidental loop back into a shell route
    would only show up under load -- this pins the invariant directly."""
    import logging

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    with caplog.at_level(logging.DEBUG, logger="splitsmith.ui.share_og"):
        resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    entries = [r for r in caplog.records if "share shell entered" in r.message]
    assert len(entries) == 1, entries
