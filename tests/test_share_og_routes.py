"""OG PNG routes on the anonymous share surface (spec 2026-08-09).

Rendering a card needs real object storage (``state.storage`` gates the
handler at 503 without it), so the happy-path tests build their own
fixture layering ``moto_s3_storage`` on top of ``hosted_env`` -- the plain
``hosted_app`` fixture has no bucket wired up, matching every other route
that doesn't touch storage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.share_card_render import FALLBACK_PNG_PATH
from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MID = "test-match-og001"
SLUG = "anna"
BUCKET = "test-og-bucket"


def _create_share(client: TestClient) -> str:
    resp = client.post(f"/api/matches/{MID}/match/shares")
    assert resp.status_code == 201
    return resp.json()["url"].rsplit("/", 1)[-1]


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the card builders
    read through (state.match() / state.shooter_project()). ``seed_match``
    alone only inserts the ownership-check ``MatchRow`` -- it does not
    create a state doc, so ``state.match()`` 404s without this."""
    from splitsmith import match_model
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
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Anna")
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


@pytest.fixture
def hosted_app_with_storage(
    hosted_env: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, _CapturingSender]]:
    """``hosted_app``, plus a moto-backed S3 bucket so ``state.storage`` is
    not None -- the PNG handlers 503 without it."""
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(tmp_path / "hosted-root"))
    with moto_s3_storage(monkeypatch, BUCKET):
        from splitsmith.ui.server import create_app

        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender


def _setup_shared_match(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> str:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share(client)
    client.cookies.clear()
    return token


def test_match_png_is_reachable_anonymously(
    hosted_env: str, hosted_app_with_storage: tuple[TestClient, _CapturingSender]
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    resp = client.get(f"/api/share/{token}/og.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == _PNG_MAGIC
    # Not just *a* PNG -- a real render, not the browser-less fallback
    # plate. CI has Chromium installed so this exercises the actual
    # render path there; on a dev box without it, this is the assertion
    # that turns a silent false-green into a real failure (see the fix
    # report's mutation proof for finding 3).
    assert resp.content != FALLBACK_PNG_PATH.read_bytes()


def test_unknown_token_png_is_404(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.get("/api/share/not-a-real-token/og.png").status_code == 404


def test_revoked_token_png_is_404(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    resp = client.post(f"/api/matches/{MID}/match/shares")
    token = resp.json()["url"].rsplit("/", 1)[-1]
    client.delete(f"/api/matches/{MID}/match/shares/{resp.json()['id']}")
    client.cookies.clear()

    assert client.get(f"/api/share/{token}/og.png").status_code == 404


def test_a_path_outside_the_allowlist_is_still_404(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The allowlist widened by exactly two shapes, not by a prefix.

    No storage needed here: both requests 404 at the ``_SHARE_PATH_RE``
    check, before any handler that would touch ``state.storage`` runs.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share(client)
    client.cookies.clear()

    # A non-numeric stage does not match the allowlist shape, so the
    # middleware 404s before routing -- if the allowlist admitted it, the
    # request would reach share_stage_png and 422 on the int(stage)
    # conversion instead, so this assertion has power to catch a regex
    # that widened too far. (A "../.." path is not used here: httpx
    # normalises it client-side, so that assertion could not fail. An
    # "ogx.png" case was dropped for the same reason: no route is
    # registered at /api/ogx.png, so it 404s whether or not the allowlist
    # admits it -- the assertion couldn't distinguish the regex causing
    # the 404 from routing causing it.)
    assert client.get(f"/api/share/{token}/og/{SLUG}/abc.png").status_code == 404


def test_stage_png_for_an_unknown_stage_falls_back_to_the_match_card(
    hosted_env: str, hosted_app_with_storage: tuple[TestClient, _CapturingSender]
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    match_resp = client.get(f"/api/share/{token}/og.png")
    stage_resp = client.get(f"/api/share/{token}/og/{SLUG}/99.png")
    # Bare content equality alone passes just as well when both requests
    # fail identically (e.g. both 404 JSON bodies, or both the browser-less
    # fallback plate) as when the fallback genuinely happened -- pinning
    # each response to a real, successful PNG render is what makes the
    # equality check mean "the same *card* rendered twice."
    for resp in (match_resp, stage_resp):
        assert resp.status_code == 200
        assert resp.content[:8] == _PNG_MAGIC
        assert resp.content != FALLBACK_PNG_PATH.read_bytes()
    assert stage_resp.content == match_resp.content


def test_png_routes_404_outside_hosted_mode() -> None:
    """No hosted env at all: create_app() unbound, same idiom as
    test_share_routes.py::test_share_local_mode_404. There is no shared
    local-mode client fixture in conftest.py."""
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/api/share/anything/og.png").status_code == 404
