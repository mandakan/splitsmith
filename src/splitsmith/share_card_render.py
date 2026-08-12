"""Render a share card and cache it, content-addressed (spec 2026-08-09).

The impure seam between the pure card model / HTML and the outside
world. The rasterizer arrives through ``overlay_raster.Rasterizer`` (a
Protocol, not a concrete import) so unit tests inject a fake and never
launch Chromium -- the same seam ``compare.mp4_grid.Runner`` uses.

**Content addressing is what makes the freshness problem disappear.**
The storage key carries a hash of everything the card displays, and the
``og:image`` URL is built from live data at request time. A re-audit
moves the figures, which moves the hash, which moves the URL -- so
Slack and X refetch rather than serving a preview of numbers nobody has
any more. Nothing needs invalidating.

A render failure never reaches the crawler as a 500: it serves the
bundled plate, leaves the cache key empty, and reports ``fell_back=True``
so the HTTP layer serves the plate with a short ``Cache-Control``. The
next request with a working browser then fills both caches -- ours and
the unfurler's -- with the real card.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from importlib.resources import files
from pathlib import Path
from typing import NamedTuple

from .overlay_raster import Rasterizer, RasterizerUnavailableError
from .overlay_theme import OverlayTheme
from .share_card import CompareCard, MatchCard, StageCard, card_hash
from .share_card_html import (
    CARD_HEIGHT,
    CARD_WIDTH,
    compare_card_html,
    match_card_html,
    stage_card_html,
)
from .storage import Storage

logger = logging.getLogger(__name__)

#: Static plate served when Chromium cannot run. Built once from
#: ``scripts/og/og.html`` and checked in, so the error path needs no
#: browser of its own.
FALLBACK_PNG_PATH: Path = Path(str(files("splitsmith.data") / "share_card_fallback.png"))


class RenderedCard(NamedTuple):
    """PNG bytes plus whether they are the real card or the bundled plate.

    ``fell_back`` exists so an HTTP caller can pick a cache header. Leaving
    it out (returning bare bytes) made the two outcomes indistinguishable
    at the seam, and ``ui/share_og.py`` then attached its one-year
    ``Cache-Control`` to both -- so a single transient Chromium failure
    during the *first* crawler fetch pinned a blank brand plate as that
    share link's preview for a year. The plate is deliberately not written
    to storage for the same reason; this flag is the missing half of that
    rule, at the layer where the cache that actually matters (the
    unfurler's, keyed by URL) is decided.
    """

    png: bytes
    fell_back: bool


def storage_key(token: str, card: MatchCard | StageCard | CompareCard, *, slug: str | None = None) -> str:
    """Content-addressed object key, scoped to the share token."""
    digest = card_hash(card)
    if isinstance(card, StageCard):
        return f"share-cards/{token}/stage-{slug}-{card.stage_number}-{digest}.png"
    if isinstance(card, CompareCard):
        return f"share-cards/{token}/compare-{card.stage_number}-{digest}.png"
    return f"share-cards/{token}/match-{digest}.png"


def render_card(
    card: MatchCard | StageCard | CompareCard, *, theme: OverlayTheme, rasterizer: Rasterizer
) -> bytes:
    """Card model to PNG bytes. Raises ``RasterizerUnavailableError``."""
    if isinstance(card, StageCard):
        html = stage_card_html(card, theme=theme)
    elif isinstance(card, CompareCard):
        html = compare_card_html(card, theme=theme)
    else:
        html = match_card_html(card, theme=theme)
    return rasterizer.png(html, width=CARD_WIDTH, height=CARD_HEIGHT)


def render_card_png(
    card: MatchCard | StageCard | CompareCard,
    *,
    theme: OverlayTheme,
    rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]],
) -> RenderedCard:
    """Render a card with no storage involved - the moment-variant path.

    Moment cards carry a continuous ``t``; writing one object per distinct
    ``t`` would let anyone holding a share token mint unbounded storage
    writes. They are rendered per fetch and HTTP-cached instead (the URL
    carries ``t`` and ``v``, so it is self-versioning). Same plate rule as
    the cached path: a fallback plate is a degraded response, flagged via
    ``fell_back`` so the route can short-cache it.
    """
    try:
        with rasterizer_factory() as rasterizer:
            return RenderedCard(png=render_card(card, theme=theme, rasterizer=rasterizer), fell_back=False)
    except RasterizerUnavailableError as exc:
        logger.warning("share card render unavailable, serving fallback plate: %s", exc.detail)
        return RenderedCard(png=FALLBACK_PNG_PATH.read_bytes(), fell_back=True)


def cached_card_png(
    card: MatchCard | StageCard | CompareCard,
    *,
    token: str,
    storage: Storage,
    theme: OverlayTheme,
    rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]],
    slug: str | None = None,
) -> RenderedCard:
    """Serve the cached PNG, rendering and writing it on a miss.

    ``rasterizer_factory`` is called ONLY on a miss. Launching Chromium
    costs about a second; taking a live rasterizer instead would pay that
    on every cache hit and leave the cache saving nothing but CPU.

    Returns a :class:`RenderedCard`, not bare bytes: the caller has to be
    able to tell a real render from the browser-less fallback plate, or it
    cannot cache the two differently -- see that class's docstring.
    """
    key = storage_key(token, card, slug=slug)
    if storage.exists(key):
        return RenderedCard(png=storage.read_bytes(key), fell_back=False)
    rendered = render_card_png(card, theme=theme, rasterizer_factory=rasterizer_factory)
    if rendered.fell_back:
        # Deliberately not cached: a browser-less host must not pin the
        # fallback plate onto this key forever. ``fell_back=True`` carries
        # the same rule outward, so the HTTP layer does not pin it in a
        # crawler's cache either. The failure is already logged in
        # render_card_png with full exception detail.
        return rendered
    storage.write_bytes(key, rendered.png)
    return rendered
