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
bundled plate and leaves the cache key empty, so the next request with a
working browser still fills it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from importlib.resources import files
from pathlib import Path

from .overlay_raster import Rasterizer, RasterizerUnavailableError
from .overlay_theme import OverlayTheme
from .share_card import MatchCard, StageCard, card_hash
from .share_card_html import CARD_HEIGHT, CARD_WIDTH, match_card_html, stage_card_html
from .storage import Storage

logger = logging.getLogger(__name__)

#: Static plate served when Chromium cannot run. Built once from
#: ``scripts/og/og.html`` and checked in, so the error path needs no
#: browser of its own.
FALLBACK_PNG_PATH: Path = Path(str(files("splitsmith.data") / "share_card_fallback.png"))


def storage_key(token: str, card: MatchCard | StageCard, *, slug: str | None = None) -> str:
    """Content-addressed object key, scoped to the share token."""
    digest = card_hash(card)
    if isinstance(card, StageCard):
        return f"share-cards/{token}/stage-{slug}-{card.stage_number}-{digest}.png"
    return f"share-cards/{token}/match-{digest}.png"


def render_card(card: MatchCard | StageCard, *, theme: OverlayTheme, rasterizer: Rasterizer) -> bytes:
    """Card model to PNG bytes. Raises ``RasterizerUnavailableError``."""
    html = (
        stage_card_html(card, theme=theme)
        if isinstance(card, StageCard)
        else match_card_html(card, theme=theme)
    )
    return rasterizer.png(html, width=CARD_WIDTH, height=CARD_HEIGHT)


def cached_card_png(
    card: MatchCard | StageCard,
    *,
    token: str,
    storage: Storage,
    theme: OverlayTheme,
    rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]],
    slug: str | None = None,
) -> bytes:
    """Serve the cached PNG, rendering and writing it on a miss.

    ``rasterizer_factory`` is called ONLY on a miss. Launching Chromium
    costs about a second; taking a live rasterizer instead would pay that
    on every cache hit and leave the cache saving nothing but CPU.
    """
    key = storage_key(token, card, slug=slug)
    if storage.exists(key):
        return storage.read_bytes(key)
    try:
        with rasterizer_factory() as rasterizer:
            data = render_card(card, theme=theme, rasterizer=rasterizer)
    except RasterizerUnavailableError as exc:
        # Deliberately not cached: a browser-less host must not pin the
        # fallback plate onto this key forever.
        logger.warning("share card render unavailable, serving fallback plate: %s", exc.detail)
        return FALLBACK_PNG_PATH.read_bytes()
    storage.write_bytes(key, data)
    return data
