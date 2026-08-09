"""Render-and-cache seam. A fake Rasterizer keeps these browser-free."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import MatchCard, RosterEntry, StageCard, stage_figures
from splitsmith.share_card_render import (
    FALLBACK_PNG_PATH,
    cached_card_png,
    storage_key,
)
from splitsmith.storage import FilesystemStorage

TOKEN = "tok_abc123"


@dataclass(frozen=True)
class _Shot:
    """Minimal stand-in for ``config.Shot``: the two attributes
    ``coach.SplitStatInterval`` reads. Building a real engine ``Shot``
    here would mean six irrelevant required fields."""

    split: float
    interval_class: str | None


class _FakeRasterizer:
    """Counts calls so a cache hit is provable, not assumed."""

    def __init__(self, payload: bytes = b"\x89PNG-fake") -> None:
        self.payload = payload
        self.calls = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls += 1
        assert (width, height) == (1200, 630)
        return self.payload


class _BrokenRasterizer:
    def __init__(self) -> None:
        self.calls = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls += 1
        raise RasterizerUnavailableError("no chromium", "no chromium, run the install hint")


class _Factory:
    """Zero-arg callable returning a context manager, standing in for
    ``ChromiumRasterizer``. ``launches`` counts how many times a browser
    would actually have been started -- a cache hit must not start one."""

    def __init__(self, rasterizer: object) -> None:
        self.rasterizer = rasterizer
        self.launches = 0

    def __call__(self) -> _Factory:
        self.launches += 1
        return self

    def __enter__(self) -> object:
        return self.rasterizer

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def theme():
    return load_theme("splitsmith")


@pytest.fixture
def store(tmp_path):
    return FilesystemStorage(tmp_path)


def _match_card() -> MatchCard:
    return MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[RosterEntry(name="Mathias Axell", division="Production Optics")],
    )


def _stage_card() -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name="Per told me to do it!",
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=3,
        stage_time=14.74,
        figures=stage_figures(
            (
                _Shot(split=1.28, interval_class="first_shot"),
                _Shot(split=0.19, interval_class="split"),
            )
        ),
    )


def test_storage_key_is_scoped_by_token_and_carries_the_hash() -> None:
    key = storage_key(TOKEN, _match_card())
    assert key.startswith(f"share-cards/{TOKEN}/match-")
    assert key.endswith(".png")


def test_stage_key_carries_slug_and_stage_number() -> None:
    key = storage_key(TOKEN, _stage_card(), slug="mathias")
    assert key.startswith(f"share-cards/{TOKEN}/stage-mathias-3-")


def test_first_call_renders_and_writes(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    rendered = cached_card_png(
        _match_card(), token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory
    )
    assert rendered.png == ras.payload
    assert rendered.fell_back is False
    assert ras.calls == 1
    assert store.exists(storage_key(TOKEN, _match_card()))


def test_second_call_serves_the_cache_without_rendering(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    card = _match_card()
    first = cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    second = cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    assert first.png == second.png
    assert second.fell_back is False
    assert ras.calls == 1


def test_a_cache_hit_never_launches_a_browser(store, theme) -> None:
    """Launching Chromium costs about a second. Paying that on a hit
    would defeat the cache, so the factory must stay uncalled."""
    factory = _Factory(_FakeRasterizer())
    card = _match_card()
    cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    assert factory.launches == 1
    cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    assert factory.launches == 1


def test_changed_figures_miss_the_cache_and_re_render(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    card = _stage_card()
    cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory, slug="m")
    moved = card.model_copy(update={"stage_name": "Short and Sweet"})
    cached_card_png(moved, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory, slug="m")
    assert ras.calls == 2


def test_rasterizer_failure_serves_the_bundled_plate(store, theme) -> None:
    ras = _BrokenRasterizer()
    rendered = cached_card_png(
        _match_card(), token=TOKEN, storage=store, theme=theme, rasterizer_factory=_Factory(ras)
    )
    assert rendered.png == FALLBACK_PNG_PATH.read_bytes()
    assert ras.calls == 1
    # The flag is the whole point: bytes alone leave the caller unable to
    # tell a degraded response from a real one, and ``ui/share_og.py`` then
    # has no way to keep its one-year Cache-Control off the plate.
    assert rendered.fell_back is True


def test_rasterizer_failure_does_not_poison_the_cache(store, theme) -> None:
    """A later working render must still be able to fill the key."""
    card = _match_card()
    cached_card_png(
        card,
        token=TOKEN,
        storage=store,
        theme=theme,
        rasterizer_factory=_Factory(_BrokenRasterizer()),
    )
    assert not store.exists(storage_key(TOKEN, card))
    good = _FakeRasterizer()
    rendered = cached_card_png(
        card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=_Factory(good)
    )
    assert rendered.png == good.payload
    assert rendered.fell_back is False


def test_the_bundled_plate_is_a_1200x630_png() -> None:
    import struct

    raw = FALLBACK_PNG_PATH.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (1200, 630)
