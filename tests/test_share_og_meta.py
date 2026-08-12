"""Meta tags on the share shells. Crawlers do not run JavaScript, so these
must be in the served HTML, not rendered by React."""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from tests.hosted_helpers import login, seed_match

MID = "test-match-meta01"
SLUG = "anna"

#: The whole SPA bundle these tests need. ``_shell`` reads
#: ``STATIC_DIR/index.html`` and splices the tags in before ``</head>``, so
#: the two things it and the assertions below actually require are a
#: ``</head>`` to splice at and the ``<div id="root">`` that proves the app
#: shell survived injection. A real Vite build supplies both plus a hashed
#: script tag nothing here reads.
_MINIMAL_INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Splitsmith</title>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""


@pytest.fixture(autouse=True)
def spa_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the share shells at a hand-written ``index.html``.

    Without this every shell test in this file needs ``pnpm build`` to have
    run first: ``share_og._shell`` 503s when ``STATIC_DIR/index.html`` is
    missing, ``src/splitsmith/ui_static/dist/`` is gitignored, and the CI
    ``test`` job installs no Node at all -- so the file was green only on a
    dev box with a stale build lying around, and would have failed on a
    clean checkout. Nothing here is testing Vite; the premise is that meta
    tags reach the *served* HTML, which a two-line document proves just as
    well as a real bundle.

    ``_shell`` does ``from .server import STATIC_DIR`` at call time, so
    patching the attribute on the server module (not rebinding a local) is
    what the handler actually reads.
    """
    from splitsmith.ui import server as server_module

    dist = tmp_path / "spa-dist"
    dist.mkdir()
    (dist / "index.html").write_text(_MINIMAL_INDEX_HTML, encoding="utf-8")
    monkeypatch.setattr(server_module, "STATIC_DIR", dist)
    yield dist


def _meta(html: str, prop: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', html)
    return m.group(1) if m else None


def _share_token(client: TestClient) -> str:
    resp = client.post(f"/api/matches/{MID}/match/shares")
    assert resp.status_code == 201
    return resp.json()["url"].rsplit("/", 1)[-1]


def _seed_state_docs(
    db_url: str,
    user_email: str,
    match_id: str,
    slug: str,
    match_name: str,
    stage_name: str = "Stage 1",
) -> None:
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
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name=stage_name)],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Anna",
            stages=[StageEntry(stage_number=1, stage_name=stage_name, time_seconds=8.0)],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)
        audit = {
            "stage_number": 1,
            "stage_name": stage_name,
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
    """Outcome-level sanity check, not the guard: a logged-in viewer of
    someone else's share link gets the same shell bytes an anonymous one
    gets. This alone has no power to catch a header-forwarding regression
    in ``_fetch_og_meta`` -- ``/api/share/`` is *also* unconditionally
    exempt from the auth gate, so the JSON this shell renders from cannot
    vary with the caller's session for a second, independent reason. The
    real guard on the sub-request itself is
    ``test_the_sub_request_carries_no_cookie_or_authorization_at_the_asgi_scope``
    below; this test stays as the end-to-end confirmation of the visible
    behavior the whole design exists to guarantee."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)

    with_session = client.get(f"/share/{token}").text
    client.cookies.clear()
    without_session = client.get(f"/share/{token}").text
    assert with_session == without_session


def test_the_sub_request_carries_no_cookie_or_authorization_at_the_asgi_scope(
    hosted_env, hosted_app, monkeypatch
) -> None:
    """Scope-level proof, stronger than pinning the ``headers=`` kwarg on
    ``.get()``: a plausible wrong implementation -- e.g. constructing
    ``httpx.AsyncClient(transport=transport, cookies=dict(request.cookies))``
    while leaving the per-call ``headers={}`` untouched -- would still put
    the viewer's session cookie on the wire, and both a kwarg-only
    assertion and the outcome-level session test above would stay green
    (the auth gate ignores it either way). Spying on
    ``ASGITransport.handle_async_request`` inspects the actual outgoing
    ASGI request headers, which is where a leaked cookie or bearer token
    would show up regardless of which httpx API attached it."""
    import httpx

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)

    captured: list[list[tuple[bytes, bytes]]] = []
    original = httpx.ASGITransport.handle_async_request

    async def _spy(self, request):
        captured.append(list(request.headers.raw))
        return await original(self, request)

    monkeypatch.setattr(httpx.ASGITransport, "handle_async_request", _spy)

    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert captured, "the ASGI sub-request was never made"
    # ``request.headers.raw`` is ``list[tuple[bytes, bytes]]`` -- decode
    # before comparing, or a str/bytes mismatch makes this assertion
    # vacuously true regardless of what was actually sent.
    names = {name.decode("latin-1").lower() for name, _value in captured[0]}
    assert "cookie" not in names
    assert "authorization" not in names


def test_the_results_shell_carries_the_same_tags_as_the_bare_token_url(hosted_env, hosted_app) -> None:
    """The SPA's client-side router redirects ``/share/{token}`` to
    ``/share/{token}/results`` immediately (App.tsx's ``index`` route), so
    that is the URL a visitor's address bar shows and would hand-copy. It
    must carry the same tags and ``noindex`` as the bare token URL, not
    fall through to a tag-free ``spa_fallback``."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}/results").text
    assert _meta(html, "og:title")
    assert _meta(html, "robots") == "noindex"
    assert "/og.png?v=" in _meta(html, "og:image")


def test_a_non_numeric_stage_falls_back_to_the_generic_shell_not_a_422(hosted_env, hosted_app) -> None:
    """Declaring ``{stage}`` as ``int`` in the route would make FastAPI 422
    a mistyped or truncated URL with a raw JSON body -- the SPA's own
    client-side route matches any string, so that URL is reachable by a
    human. A malformed stage must still render *a page*, just the generic
    one, exactly like an unknown token does."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    resp = client.get(f"/share/{token}/results/{SLUG}/not-a-number")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert _meta(resp.text, "og:title") == "Splitsmith"


@pytest.mark.parametrize("stage", ["not-a-number", "²", "¹²³", "-1", "1.0"])
def test_a_stage_segment_int_cannot_parse_still_serves_a_page(hosted_env, hosted_app, stage: str) -> None:
    """``str.isdigit()`` is True for superscripts (``²``, ``³``, ``¹``)
    while ``int()`` rejects them, so gating on ``isdigit`` let ``²`` through
    to an unhandled ``ValueError`` -- a 500 on an anonymous, uncontrolled
    public URL. ``not-a-number`` (already covered above) cannot catch that:
    it fails both predicates. The superscript cases are the ones that
    discriminate ``isdigit`` from ``isdecimal``; the rest pin the shape the
    docstring promises."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    resp = client.get(f"/share/{token}/results/{SLUG}/{stage}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    assert _meta(resp.text, "og:title") == "Splitsmith"


def test_a_slow_card_build_still_returns_promptly_with_the_generic_shell(
    hosted_env, hosted_app, monkeypatch
) -> None:
    """``httpx.ASGITransport`` ignores httpx's own network ``timeout=``
    entirely -- it calls the app in-process, no socket involved. Without an
    explicit bound enforced on our side, a slow storage or DB call inside
    the card build holds a crawler's (or a browser's) connection open
    indefinitely. Shrink the timeout and make the card builder sleep well
    past it, then confirm the shell still comes back promptly, degraded to
    the generic tags rather than waiting out the full sleep."""
    import time

    from splitsmith.ui import share_og

    monkeypatch.setattr(share_og, "_SUB_REQUEST_TIMEOUT_S", 0.2)

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    def _slow_build_match_card(state):
        time.sleep(1.5)
        return share_og.MatchCard(match_name="should not be reached", stage_count=0)

    monkeypatch.setattr(share_og, "build_match_card", _slow_build_match_card)

    started = time.monotonic()
    resp = client.get(f"/share/{token}")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert elapsed < 1.0, elapsed
    assert _meta(resp.text, "og:title") == "Splitsmith"


def test_a_crashing_card_build_degrades_to_the_generic_shell_not_a_500(
    hosted_env, hosted_app, monkeypatch
) -> None:
    """A live token whose card build crashes must be indistinguishable from
    an unknown token -- a 500 here would be a token-existence oracle
    (exactly what ``_png_response`` already avoids on the PNG side), and it
    would take the whole page down for a real browser where the
    data-independent ``spa_fallback`` used to serve it unconditionally."""
    from splitsmith.ui import share_og

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    def _boom(state):
        raise RuntimeError("card build blew up")

    monkeypatch.setattr(share_og, "build_match_card", _boom)

    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert _meta(resp.text, "og:title") == "Splitsmith"


def test_a_match_name_with_markup_does_not_break_out_of_the_meta_tag(hosted_env, hosted_app) -> None:
    """Match and shooter names are untrusted input rendered into a page
    served to arbitrary anonymous viewers. Every interpolated value must
    go through ``html.escape`` -- a quote or an angle bracket in a name
    must not become an attribute or tag breakout."""
    payload = 'Ma"tch <&> </title>"><script>alert(1)</script>'
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env, match_name=payload)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}").text
    assert "<script>alert(1)</script>" not in html
    assert '"><script>' not in html
    assert payload not in html
    title = _meta(html, "og:title")
    assert title is not None
    # The *decoded* value round-trips (this is really the same match name);
    # the raw HTML bytes above must never contain it unescaped.
    assert html_lib.unescape(title) == payload


def test_a_stage_name_with_markup_does_not_break_out_of_the_meta_tag(hosted_env, hosted_app) -> None:
    payload = 'Stage <&> "><script>alert(2)</script>'
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG, f"Test match {MID}", stage_name=payload)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}/results/{SLUG}/1").text
    assert "<script>alert(2)</script>" not in html
    assert '"><script>' not in html
    assert payload not in html


def test_stage_meta_with_moment_suffixes_title_and_image(hosted_env, hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    resp = client.get(f"/api/share/{token}/og-meta/{SLUG}/1?t=4.32")
    assert resp.status_code == 200
    meta = resp.json()
    assert meta["title"].endswith(" - moment at 4.32s")
    assert "&t=4.32" in meta["image_path"]


def test_stage_shell_forwards_t_to_og_meta(hosted_env, hosted_app, monkeypatch) -> None:
    from splitsmith.ui import share_og

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    seen: list[str] = []

    async def _capture(request, path):
        seen.append(path)
        return None

    monkeypatch.setattr(share_og, "_fetch_og_meta", _capture)
    client.get(f"/share/{token}/results/{SLUG}/1?t=4.32")
    assert seen == [f"/api/share/{token}/og-meta/{SLUG}/1?t=4.32"]


def test_stage_shell_drops_junk_t(hosted_env, hosted_app, monkeypatch) -> None:
    """?t=abc -> og_meta_path has no query string."""
    from splitsmith.ui import share_og

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed(hosted_env)
    token = _share_token(client)
    client.cookies.clear()

    seen: list[str] = []

    async def _capture(request, path):
        seen.append(path)
        return None

    monkeypatch.setattr(share_og, "_fetch_og_meta", _capture)
    client.get(f"/share/{token}/results/{SLUG}/1?t=abc")
    assert seen == [f"/api/share/{token}/og-meta/{SLUG}/1"]


def test_new_share_og_routes_404_outside_hosted_mode() -> None:
    """The whole share-og surface -- both og-meta JSON endpoints and all
    three shells -- is hosted-only, same idiom as the PNG routes. The
    existing local-mode lock (test_share_routes.py::test_share_local_mode_404)
    only covers ``match/shooters``; this pins the four routes Task 7 added
    plus the ``/results`` shell from finding 7."""
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/api/share/anything/og-meta").status_code == 404
        assert client.get("/api/share/anything/og-meta/anna/1").status_code == 404
        assert client.get("/share/anything").status_code == 404
        assert client.get("/share/anything/results").status_code == 404
        assert client.get("/share/anything/results/anna/1").status_code == 404


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
