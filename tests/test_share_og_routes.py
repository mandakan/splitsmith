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


def test_the_fallback_plate_is_not_served_behind_a_year_long_cache(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crawler caches ``og:image`` by URL, and the URL is content-addressed
    -- it only moves when the shooter's data moves. So a one-year
    ``Cache-Control`` on the browser-less fallback plate pins a blank brand
    card as that share link's preview until the next re-audit, off a single
    transient Chromium failure that happened to land on the first crawler
    fetch. ``share_card_render`` already refuses to write the plate to
    object storage for this reason; this pins the same rule at the HTTP
    layer, which is the cache that decides what a viewer actually sees.

    The share-creation warm has already filled the match card's key, so the
    degraded fetch below moves the card (a different match name hashes to a
    different key) to force the miss the fallback needs.
    """
    from splitsmith.overlay_raster import RasterizerUnavailableError
    from splitsmith.ui import share_og

    token = _setup_shared_match(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    real = client.get(f"/api/share/{token}/og.png")
    assert real.status_code == 200
    assert real.content != FALLBACK_PNG_PATH.read_bytes(), "not a real render -- no Chromium?"
    assert real.headers["cache-control"] == "public, max-age=31536000"

    def _moved_card(state: object) -> share_og.MatchCard:
        return share_og.MatchCard(match_name="A card no key holds yet", stage_count=1)

    def _no_browser() -> object:
        raise RasterizerUnavailableError("no chromium", "no chromium, run the install hint")

    monkeypatch.setattr(share_og, "build_match_card", _moved_card)
    monkeypatch.setattr(share_og, "_chromium_factory", _no_browser)

    degraded = client.get(f"/api/share/{token}/og.png")
    assert degraded.status_code == 200
    assert degraded.content == FALLBACK_PNG_PATH.read_bytes()
    assert degraded.headers["cache-control"] != real.headers["cache-control"]
    max_age = int(degraded.headers["cache-control"].rsplit("=", 1)[1])
    assert 0 < max_age <= 300, f"the plate must expire in minutes, not {max_age}s"


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


def _seed_legacy_stage_audit(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Match + project (with a real stage 1, so ``project.stage(1)``
    resolves) + a legacy-shaped audit doc: every shot carries
    ``ms_after_beep``, none carries ``interval_class`` -- the shape an
    audit doc had before #775 started classifying on save.
    """
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
            name=f"Test match {match_id}",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Anna",
            stages=[StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=8.0)],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)
        # The 564ms -> 1064ms gap is engineered so the two computation
        # paths disagree at the auto-classifier's 0.5s split ceiling:
        # coach.classify_intervals_in_dicts computes (1064-564)/1000.0
        # == 0.5 exactly (<=0.5 -> "split"), while
        # audit_shots_to_engine_shots computes 1.064-0.564 ==
        # 0.5000000000000001 (just over 0.5, excluded by the
        # unclassified fallback in coach.statistic_splits). That
        # float-precision gap is what lets a test on the *figures* tell
        # the healed (classified) path apart from the raw (threshold)
        # one -- a gap both rules agree on would prove nothing.
        audit = {
            "stage_number": 1,
            "stage_name": "Stage 1",
            "shots": [
                {"shot_number": 1, "ms_after_beep": 564},
                {"shot_number": 2, "ms_after_beep": 1064},
                {"shot_number": 3, "ms_after_beep": 1264},
                {"shot_number": 4, "ms_after_beep": 1464},
            ],
        }
        await store.save_audit(match_id, slug, 1, audit, expected_version=0)

    asyncio.run(_seed())


def _load_audit_doc(db_url: str, user_email: str, match_id: str, slug: str) -> tuple[dict, int]:
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _load() -> tuple[dict, int]:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        doc, version = await store.load_audit(match_id, slug, 1)
        assert doc is not None
        return doc, version

    return asyncio.run(_load())


def test_a_legacy_unclassified_audit_is_healed_for_the_card(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage audited before #775 has no interval_class. The coach GET
    heals such docs in memory for share reads; the card must do the same,
    or the preview shows different numbers than the page it links to."""
    from splitsmith.ui import share_og

    client, sender = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_legacy_stage_audit(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share(client)
    client.cookies.clear()

    doc_before, version_before = _load_audit_doc(hosted_env, "owner@example.com", MID, SLUG)

    captured: list = []
    original_build_stage_card = share_og.build_stage_card

    def _capturing_build_stage_card(state: object, slug: str, stage_number: int, **kwargs: object):
        card = original_build_stage_card(state, slug, stage_number, **kwargs)
        captured.append(card)
        return card

    monkeypatch.setattr(share_og, "build_stage_card", _capturing_build_stage_card)

    # og-meta, not og.png: it exercises build_stage_card through the real
    # anonymous share path (middleware sets current_tenant/current_match_id)
    # without needing Chromium to render a PNG.
    resp = client.get(f"/api/share/{token}/og-meta/{SLUG}/1")
    assert resp.status_code == 200, resp.text

    assert len(captured) == 1
    card = captured[0]
    assert card is not None

    # The healed (classified) figures, not the raw threshold ones: shot 2's
    # gap resolves to a split under the classifier's own arithmetic (see
    # the comment in _seed_legacy_stage_audit), so all three post-draw
    # intervals count.
    assert card.figures.source == "coach"
    assert card.figures.split_count == 3
    assert card.figures.avg_split == pytest.approx(0.3)
    assert card.figures.draw == pytest.approx(0.564)

    # The share read must never persist the heal: the stored doc is
    # unchanged (same version, same content) after building the card.
    doc_after, version_after = _load_audit_doc(hosted_env, "owner@example.com", MID, SLUG)
    assert version_after == version_before
    assert doc_after == doc_before
    assert all(s.get("interval_class") is None for s in doc_after["shots"])


def _setup_shared_stage(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> str:
    """Same shape as ``_setup_shared_match``, but the shooter's stage 1
    carries real audited shots - ``_seed_state_docs`` alone (used by
    ``_setup_shared_match``) leaves ``MatchProject`` with no stages at
    all, so ``build_stage_card`` always returns ``None`` and every
    request falls back to the match card, which would make the moment-t
    plumbing below untestable."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_legacy_stage_audit(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share(client)
    client.cookies.clear()
    return token


def test_moment_stage_png_renders_without_a_storage_write(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moment card is rendered per fetch, never written to object
    storage - see ``render_card_png``'s docstring on why an unbounded
    ``t`` cannot be cached by key. The share-creation warm already wrote
    the moment-free match card under ``share-cards/``, so this pins that
    the count does not move again on top of that."""
    import boto3

    from splitsmith.ui import share_og

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    calls: list[object] = []
    real = share_og.render_card_png

    def _spy(card, **kwargs):
        calls.append(card)
        return real(card, **kwargs)

    monkeypatch.setattr(share_og, "render_card_png", _spy)

    s3 = boto3.client("s3", region_name="us-east-1")
    put_count_before = s3.list_objects_v2(Bucket=BUCKET, Prefix="share-cards/").get("KeyCount", 0)

    resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=4.32")

    assert resp.status_code == 200
    assert calls and calls[0].moment_t == 4.32

    put_count_after = s3.list_objects_v2(Bucket=BUCKET, Prefix="share-cards/").get("KeyCount", 0)
    assert put_count_after == put_count_before


def test_moment_t_is_clamped_and_rounded(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """t=999999 clamps to 3600.0; t=-500 clamps to -60.0; junk t falls back
    to the cached moment-free card (route behaves as if t were absent)."""
    from splitsmith.ui import share_og

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    calls: list[object] = []
    real = share_og.render_card_png

    def _spy(card, **kwargs):
        calls.append(card)
        return real(card, **kwargs)

    monkeypatch.setattr(share_og, "render_card_png", _spy)

    resp_high = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=999999")
    assert resp_high.status_code == 200
    assert calls[-1].moment_t == 3600.0

    resp_low = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=-500")
    assert resp_low.status_code == 200
    assert calls[-1].moment_t == -60.0

    # Junk t must behave exactly as if t were absent: the cached,
    # storage-backed path (not render_card_png at all), same bytes and
    # same year-long cache header as a request with no t.
    no_t = client.get(f"/api/share/{token}/og/{SLUG}/1.png")
    junk = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=abc")
    assert junk.status_code == 200
    assert junk.content == no_t.content
    assert junk.headers["cache-control"] == no_t.headers["cache-control"]


def test_junk_t_serves_the_plain_stage_card(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from splitsmith.ui import share_og

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    calls: list[float | None] = []
    real = share_og.build_stage_card

    def _spy(state, slug, stage_number, **kwargs):
        calls.append(kwargs.get("moment_t"))
        return real(state, slug, stage_number, **kwargs)

    monkeypatch.setattr(share_og, "build_stage_card", _spy)

    resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=abc")

    assert resp.status_code == 200
    assert calls == [None]


def test_png_routes_404_outside_hosted_mode() -> None:
    """No hosted env at all: create_app() unbound, same idiom as
    test_share_routes.py::test_share_local_mode_404. There is no shared
    local-mode client fixture in conftest.py."""
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/api/share/anything/og.png").status_code == 404


def test_creating_a_share_warms_the_match_card(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link the owner pastes previews without a cold render.

    ``state.storage`` only resolves inside a pinned-tenant request (it
    reads the ``current_tenant`` contextvar, which resets once the
    request that set it returns), so this can't assert
    ``storage.exists(...)`` from the test body directly. Instead it
    poisons ``share_card_render.render_card`` -- the only thing that
    would run on a cache *miss* -- then fetches the PNG the share just
    created. If share-creation warmed the cache, the fetch is a hit and
    never reaches the poisoned function; if it didn't, the fetch is a
    miss, ``render_card`` runs, and the poison fires.
    """
    import splitsmith.share_card_render as share_card_render_mod
    import splitsmith.ui.share_og as share_og

    # The warm runs a real Chromium launch on a worker thread and
    # ``warm_match_card_bounded`` stops *waiting* for it after
    # ``_WARM_TIMEOUT_S`` -- it cannot cancel it. On a busy machine the
    # launch outruns the 3 s default, share creation returns with the
    # cache still cold, and the fetch below lands on the poisoned
    # ``render_card``. That made this test fail under parallel load while
    # passing standalone and in CI.
    #
    # The subject here is *that* creating a share warms the card, not that
    # it does so within any particular deadline. Raising the bound removes
    # the race without weakening the assertion; the deadline behaviour is
    # covered separately and explicitly by
    # ``test_share_creation_does_not_wait_out_a_slow_warm``, which pins it
    # from the other side with a 0.1 s bound.
    monkeypatch.setattr(share_og, "_WARM_TIMEOUT_S", 60.0)

    token = _setup_shared_match(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    def _unwarmed(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("render_card was called: the match card was not pre-warmed")

    monkeypatch.setattr(share_card_render_mod, "render_card", _unwarmed)

    resp = client.get(f"/api/share/{token}/og.png")
    assert resp.status_code == 200
    assert resp.content[:8] == _PNG_MAGIC


def test_share_creation_still_succeeds_when_rendering_fails(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warming is best-effort: a browser-less host must still hand the
    owner a working link.

    ``_seed_state_docs`` matters here beyond the other tests in this
    file: without it, ``build_match_card`` 404s before ``warm_match_card``
    ever reaches ``cached_card_png``, and the monkeypatch below would go
    unexercised while the test still passed for the wrong reason. The
    ``calls`` list makes that failure mode visible instead of assumed.
    """
    import splitsmith.ui.share_og as share_og

    calls: list[str] = []

    def _boom(*args: object, **kwargs: object) -> bytes:
        calls.append(str(kwargs.get("token")))
        raise RuntimeError("no browser here")

    monkeypatch.setattr(share_og, "cached_card_png", _boom)

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)

    assert client.post(f"/api/matches/{MID}/match/shares").status_code == 201
    assert calls, "cached_card_png was never called -- the monkeypatch was not exercised"


def test_an_abandoned_warm_never_reaches_asyncio_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """``warm_match_card_bounded``'s docstring promises that *every*
    failure is logged there at ``warning`` and never re-raised. Abandoning
    the task without cancelling it broke that promise for the one case
    where both things go wrong: a warm that times out and *then* raises
    finishes unretrieved, so asyncio's ``Task.__del__`` logs "Task
    exception was never retrieved" with a full traceback at ``ERROR`` --
    on exactly the degraded host where the timeout fires, which is where a
    spurious ERROR is most expensive to an operator triaging a real
    incident.

    The loop's exception handler is where that report lands, so installing
    one and asserting it stays empty tests the outcome (nothing at ERROR),
    not the mechanism (that ``cancel`` was called).
    """
    import gc
    import time

    import splitsmith.ui.share_og as share_og

    monkeypatch.setattr(share_og, "_WARM_TIMEOUT_S", 0.05)

    def _slow_then_boom(state: object, token: str) -> None:
        time.sleep(0.3)
        raise RuntimeError("the render failed after the caller gave up")

    monkeypatch.setattr(share_og, "warm_match_card", _slow_then_boom)

    handled: list[dict[str, object]] = []

    async def _drive() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: handled.append(context))
        await share_og.warm_match_card_bounded(object(), "tok_abandoned")
        # Outlive the warm so it really does raise in its worker thread,
        # then force the collection that runs Task.__del__.
        await asyncio.sleep(0.6)
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(_drive())

    assert not handled, handled


def test_compare_png_is_reachable_anonymously(
    hosted_env: str, hosted_app_with_storage: tuple[TestClient, _CapturingSender]
) -> None:
    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    resp = client.get(f"/api/share/{token}/og/compare/1.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_compare_png_with_moment_skips_storage(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shape as ``test_moment_stage_png_renders_without_a_storage_write``
    - a moment-carrying compare card is rendered per fetch, never written to
    object storage."""
    import boto3

    from splitsmith.ui import share_og

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    calls: list[object] = []
    real = share_og.render_card_png

    def _spy(card, **kwargs):
        calls.append(card)
        return real(card, **kwargs)

    monkeypatch.setattr(share_og, "render_card_png", _spy)

    s3 = boto3.client("s3", region_name="us-east-1")
    put_count_before = s3.list_objects_v2(Bucket=BUCKET, Prefix="share-cards/").get("KeyCount", 0)

    resp = client.get(f"/api/share/{token}/og/compare/1.png?t=2.5")

    assert resp.status_code == 200
    assert calls and calls[0].moment_t == 2.5

    put_count_after = s3.list_objects_v2(Bucket=BUCKET, Prefix="share-cards/").get("KeyCount", 0)
    assert put_count_after == put_count_before


def test_compare_png_for_an_unknown_stage_falls_back_to_the_match_card(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
) -> None:
    """Regression test: ``build_compare_card`` must validate ``stage_number``
    against the match before rendering anything. Without that check, a
    caller holding nothing but the share token could iterate ``N`` through
    ``GET /api/share/{token}/og/compare/{N}.png`` and, moment- and
    who-free, mint one cached object per distinct ``N`` under
    ``share-cards/{token}/compare-{N}-*`` - unbounded storage writes from a
    fabricated "Stage N" card that was never real. An unknown stage must
    instead fall back to the match card, exactly like ``build_stage_card``'s
    unknown-stage behavior (``test_stage_png_for_an_unknown_stage_falls_back_to_the_match_card``)."""
    import boto3

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    s3 = boto3.client("s3", region_name="us-east-1")

    match_resp = client.get(f"/api/share/{token}/og.png")
    compare_resp = client.get(f"/api/share/{token}/og/compare/999.png")
    for resp in (match_resp, compare_resp):
        assert resp.status_code == 200
        assert resp.content[:8] == _PNG_MAGIC
        assert resp.content != FALLBACK_PNG_PATH.read_bytes()
    assert compare_resp.content == match_resp.content

    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"share-cards/{token}/")
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    assert not any(f"share-cards/{token}/compare-" in key for key in keys)

    meta_resp = client.get(f"/api/share/{token}/og-meta/compare/999")
    match_meta_resp = client.get(f"/api/share/{token}/og-meta")
    assert meta_resp.status_code == 200
    assert match_meta_resp.status_code == 200
    assert meta_resp.json()["title"] == match_meta_resp.json()["title"]


def test_compare_route_wins_over_a_shooter_slugged_compare(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal 'compare' segment must dispatch to the compare card even
    though /api/og/{slug}/{stage}.png would also match 'compare' as a slug
    if it were registered first. Spy on build_compare_card and assert it
    ran (rather than build_stage_card, which would run if the shooter-slug
    route won instead)."""
    from splitsmith.ui import share_og

    token = _setup_shared_stage(hosted_env, hosted_app_with_storage)
    client, _sender = hosted_app_with_storage

    compare_calls: list[object] = []
    real_compare = share_og.build_compare_card

    def _spy_compare(state, stage_number, **kwargs):
        card = real_compare(state, stage_number, **kwargs)
        compare_calls.append(card)
        return card

    stage_calls: list[object] = []
    real_stage = share_og.build_stage_card

    def _spy_stage(state, slug, stage_number, **kwargs):
        card = real_stage(state, slug, stage_number, **kwargs)
        stage_calls.append(card)
        return card

    monkeypatch.setattr(share_og, "build_compare_card", _spy_compare)
    monkeypatch.setattr(share_og, "build_stage_card", _spy_stage)

    resp = client.get(f"/api/share/{token}/og/compare/1.png")

    assert resp.status_code == 200
    assert compare_calls, "build_compare_card was never called"
    assert not stage_calls, "build_stage_card ran - the {slug} route won instead of /compare"


def test_share_creation_returns_promptly_when_warming_is_slow(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``warm_match_card_bounded``'s timeout unblocks the response -- it
    does not, and cannot, cancel the in-flight warm (see that function's
    docstring). A slow or stuck render must not make the owner wait for
    it: this pins the response time, not just the eventual status code.
    """
    import time

    import splitsmith.ui.share_og as share_og

    monkeypatch.setattr(share_og, "_WARM_TIMEOUT_S", 0.1)

    def _slow(*args: object, **kwargs: object) -> None:
        time.sleep(2.0)

    monkeypatch.setattr(share_og, "warm_match_card", _slow)

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)

    start = time.monotonic()
    resp = client.post(f"/api/matches/{MID}/match/shares")
    elapsed = time.monotonic() - start

    assert resp.status_code == 201
    body = resp.json()
    assert body["url"], "share creation must still hand back a working share URL"
    assert "/share/" in body["url"]
    assert elapsed < 1.0, f"response should not wait out the slow warm: took {elapsed:.2f}s"
