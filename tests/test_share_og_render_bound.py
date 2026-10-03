"""Share card renders are bounded per process and repeats are served from memory.

Every card render launches Chromium. The moment (``t``) and roster
(``who``) variants are rendered per fetch by design (they never reach
object storage), so ``share_og`` keeps two guards in front of the
rasterizer: at most ``CARD_RENDER_CONCURRENCY`` renders run at once, a
request that cannot get a slot within ``CARD_RENDER_WAIT_S`` is answered
503 with ``Retry-After`` rather than queued, and an identical moment card
is served from a small in-process cache instead of being rendered again.

The rasterizer is replaced by a counting fake so these tests never launch
a browser and can count renders exactly.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match
from tests.test_share_og_routes import BUCKET, MID, SLUG, _create_share, _seed_legacy_stage_audit

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-card"


@pytest.fixture
def hosted_app_with_storage(
    hosted_env: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, _CapturingSender]]:
    """``hosted_app`` plus a moto-backed bucket; the PNG routes need storage."""
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(tmp_path / "hosted-root"))
    with moto_s3_storage(monkeypatch, BUCKET):
        from splitsmith.ui.server import create_app

        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender


class _CountingRasterizer:
    def __init__(self) -> None:
        self.renders = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.renders += 1
        return _FAKE_PNG


@pytest.fixture
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[_CountingRasterizer]:
    from splitsmith.ui import share_og

    rasterizer = _CountingRasterizer()

    @contextmanager
    def _factory() -> Iterator[_CountingRasterizer]:
        yield rasterizer

    monkeypatch.setattr(share_og, "_chromium_factory", _factory)
    share_og.clear_rendered_card_cache()
    yield rasterizer
    share_og.clear_rendered_card_cache()


def _seed_owner(hosted_env: str, client: TestClient, sender: _CapturingSender) -> None:
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_legacy_stage_audit(hosted_env, "owner@example.com", MID, SLUG)


def test_a_repeated_moment_card_is_rendered_once(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    fake_browser: _CountingRasterizer,
) -> None:
    client, sender = hosted_app_with_storage
    _seed_owner(hosted_env, client, sender)
    token = _create_share(client)
    client.cookies.clear()
    before = fake_browser.renders

    stage = [client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": "4.32"}) for _ in range(3)]
    assert [r.status_code for r in stage] == [200, 200, 200]
    assert {r.content for r in stage} == {_FAKE_PNG}
    assert fake_browser.renders == before + 1

    # "4.320" parses to the same moment, so it is the same card.
    assert client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": "4.320"}).status_code == 200
    assert fake_browser.renders == before + 1

    compare = [
        client.get(f"/api/share/{token}/og/compare/1.png", params={"t": "2.50", "who": SLUG})
        for _ in range(3)
    ]
    assert [r.status_code for r in compare] == [200, 200, 200]
    assert fake_browser.renders == before + 2

    # A different moment is a different card.
    assert client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": "4.33"}).status_code == 200
    assert fake_browser.renders == before + 3


def test_saturated_card_renders_answer_503_with_retry_after(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    fake_browser: _CountingRasterizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from splitsmith.ui import share_og

    client, sender = hosted_app_with_storage
    _seed_owner(hosted_env, client, sender)

    slots = threading.BoundedSemaphore(share_og.CARD_RENDER_CONCURRENCY)
    monkeypatch.setattr(share_og, "_render_slots", slots)
    monkeypatch.setattr(share_og, "CARD_RENDER_WAIT_S", 0.05)
    for _ in range(share_og.CARD_RENDER_CONCURRENCY):
        assert slots.acquire(timeout=1)

    # The share-creation warm goes through the same bound: the link is
    # still created, the warm just does not render.
    token = _create_share(client)
    client.cookies.clear()
    assert fake_browser.renders == 0

    busy = [
        client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": "1.00"}),
        client.get(f"/api/share/{token}/og/compare/1.png", params={"who": SLUG}),
        # Moment-free cards on a storage miss render too.
        client.get(f"/api/share/{token}/og/{SLUG}/1.png"),
        client.get(f"/api/share/{token}/og.png"),
    ]
    for resp in busy:
        assert resp.status_code == 503, resp.text
        assert int(resp.headers["retry-after"]) > 0
    assert fake_browser.renders == 0

    for _ in range(share_og.CARD_RENDER_CONCURRENCY):
        slots.release()
    resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": "1.00"})
    assert resp.status_code == 200
    assert resp.content == _FAKE_PNG
    assert fake_browser.renders == 1


def test_render_slots_are_released_after_each_render(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender],
    fake_browser: _CountingRasterizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More sequential renders than slots all succeed: a render gives its
    slot back, including one whose rasterizer raised."""
    from splitsmith.overlay_raster import RasterizerUnavailableError
    from splitsmith.ui import share_og

    client, sender = hosted_app_with_storage
    _seed_owner(hosted_env, client, sender)
    slots = threading.BoundedSemaphore(share_og.CARD_RENDER_CONCURRENCY)
    monkeypatch.setattr(share_og, "_render_slots", slots)
    monkeypatch.setattr(share_og, "CARD_RENDER_WAIT_S", 0.05)
    token = _create_share(client)
    client.cookies.clear()

    for i in range(share_og.CARD_RENDER_CONCURRENCY * 2):
        resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": f"{i + 1}.00"})
        assert resp.status_code == 200

    @contextmanager
    def _no_browser() -> Iterator[object]:
        raise RasterizerUnavailableError("no chromium", "install hint")
        yield  # pragma: no cover

    monkeypatch.setattr(share_og, "_chromium_factory", _no_browser)
    for i in range(share_og.CARD_RENDER_CONCURRENCY * 2):
        resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png", params={"t": f"{i + 20}.00"})
        assert resp.status_code == 200
    for _ in range(share_og.CARD_RENDER_CONCURRENCY):
        assert slots.acquire(timeout=0.01)
