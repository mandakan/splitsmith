"""Hosted-only share-card routes: the card PNGs served on the anonymous
share surface (spec 2026-08-09).

Same always-registered idiom as ``sync_api`` and ``device_auth_api``:
every route 404s outside hosted mode. The db-import laziness that idiom
is usually about doesn't apply here -- this module has no db import to
defer, hosted or otherwise. What *is* deferred is the ``.server`` import
in ``_hosted_gate`` (breaks an import cycle: ``server.create_app``
imports this module) and the Chromium *launch* in ``_chromium_factory``
(``playwright`` itself is a core dependency, already imported
transitively via ``share_card_render`` -> ``overlay_raster`` by the time
this module loads; only the browser process start is deferred, and only
until a cache miss -- see that function's docstring).

The two PNG paths are on the anonymous share surface, so they are also
listed in ``server._SHARE_PATH_RE`` -- that regex is the containment
boundary, and both routes qualify: read-only, match-scoped, and the
client never supplies a match id (the share middleware has already
pinned the tenant + match by the time these handlers run).

Routes are registered at the plain ``/api/og.png`` / ``/api/og/{slug}/
{stage}.png`` paths, not at ``/api/share/{token}/...``. The public URL
a browser calls is ``/api/share/{token}/og.png``, but by the time a
request reaches the FastAPI router it has been rewritten twice: first
by ``_share_alias`` (which resolves the token and rewrites onto
``/api/matches/{match_id}/{rest}``), then by ``_match_id_alias`` (which
strips that prefix down to plain ``/api/{rest}``). Every other route in
``_SHARE_PATH_RE`` -- ``/api/match/shooters``, ``/api/shooters/{slug}/
project``, etc. -- is registered the same way. ``_share_alias`` stashes
the raw token on ``request.state.share_token`` since it falls out of
the URL after that first rewrite and the card cache key needs it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..audit_data import audit_shots_to_engine_shots
from ..overlay_theme import load_theme
from ..share_card import MatchCard, RosterEntry, StageCard, stage_figures
from ..share_card_render import cached_card_png

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from ..overlay_raster import Rasterizer

logger = logging.getLogger(__name__)

router = APIRouter()


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode. Lazy import, same as sync_api."""
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _state(request: Request) -> Any:
    return request.app.state.splitsmith_state


def build_match_card(state: Any) -> MatchCard:
    """Identity plus roster. No aggregate time figure by design.

    Division is not persisted per shooter (it only ever arrives with a
    live scoreboard fetch, which this route never makes), so every
    roster entry carries ``division=None``.
    """
    match = state.match()
    roster: list[RosterEntry] = []
    for slug in match.shooters:
        try:
            project = state.shooter_project(slug)
            name = project.competitor_name or slug
        except HTTPException:
            # A shooter listed on the match but with no project doc yet
            # (e.g. just added to the roster). Degrade to the slug
            # rather than dropping the shooter or failing the whole card.
            name = slug
        roster.append(RosterEntry(name=name, division=None))
    return MatchCard(
        match_name=match.name or "Splitsmith match",
        match_date=match.match_date.isoformat() if match.match_date else None,
        stage_count=len(match.stages),
        roster=roster,
    )


def build_stage_card(state: Any, slug: str, stage_number: int) -> StageCard | None:
    """``None`` for an unknown slug or stage, or a stage with no shots --
    the caller then serves the match card instead."""
    try:
        project = state.shooter_project(slug)
        stg = project.stage(stage_number)
    except (KeyError, ValueError, HTTPException):
        return None
    payload, _version = state.load_audit(slug, stage_number)
    if not payload:
        return None
    # #774's canonical converter -- it carries interval_class onto each
    # engine Shot, which is what makes the split rule reachable here. The
    # beep offset only affects ``time_absolute``, which no card reads, so
    # 0.0 is correct rather than merely harmless.
    shots = audit_shots_to_engine_shots(payload, beep_time_in_source=0.0)
    if not shots:
        return None
    figures = stage_figures(shots)
    return StageCard(
        stage_number=stage_number,
        stage_name=stg.stage_name or f"Stage {stage_number}",
        shooter_name=project.competitor_name or slug,
        match_name=state.match().name or "Splitsmith match",
        shot_count=len(shots),
        # ``StageCard.stage_time`` is ``float | None`` precisely so the
        # card can omit it; the HTML only omits it for ``None``. A
        # placeholder stage carries ``time_seconds=0.0`` (never negative),
        # which is not a real stage time worth rendering -- ``or None``
        # turns that falsy-but-"set" value into the omit case.
        stage_time=stg.time_seconds or None,
        figures=figures,
    )


#: The URL carries the card hash as ``?v=``, so a changed card is a
#: changed URL and long caching is safe. ``immutable`` is deliberately
#: absent: the meta tag is rendered from live data a moment before the
#: crawler fetches the image, so a given ``?v`` can in principle be
#: served after another write. Long-lived, but revalidatable.
_PNG_HEADERS = {"Cache-Control": "public, max-age=31536000"}


def _chromium_factory() -> AbstractContextManager[Rasterizer]:
    # Lazy import breaks an import cycle with .server (share_og is imported
    # from create_app), not for local-slim's sake -- playwright is a core
    # dependency, so it's already in sys.modules by the time this runs
    # (share_card_render imports overlay_raster imports playwright.sync_api
    # at module scope). What's actually deferred here is the browser
    # *launch*: ChromiumRasterizer() doesn't start Chromium until its
    # __enter__ runs, and cached_card_png only calls this factory on a
    # cache miss, so a hit never pays the ~1s launch cost.
    from ..overlay_raster import ChromiumRasterizer

    return ChromiumRasterizer()


def _png_response(state: Any, token: str, card: MatchCard | StageCard, slug: str | None) -> Response:
    if state.storage is None:
        # Not a 503: the anonymous surface's invariant is that every
        # failure looks the same (_share_alias rewrites only a 404 into
        # the opaque body). A 503 here would let a caller distinguish "no
        # such token" from "token is real but storage isn't wired up" --
        # a token-existence oracle. Log the real reason for an operator;
        # 404 for everyone else.
        logger.warning("share card render unavailable: state.storage is None (token=%s)", token)
        raise HTTPException(status_code=404, detail="not found")
    data = cached_card_png(
        card,
        token=token,
        storage=state.storage,
        theme=load_theme("splitsmith"),
        rasterizer_factory=_chromium_factory,
        slug=slug,
    )
    return Response(content=data, media_type="image/png", headers=_PNG_HEADERS)


def _share_token(request: Request) -> str:
    """The raw share token, stashed by ``_share_alias`` on ``request.state``
    before it rewrites the URL down to the plain ``/api/{rest}`` path these
    handlers are registered under. Missing only if this route were somehow
    reached outside the share middleware, which ``_SHARE_PATH_RE`` and the
    route registration below rule out -- 404, not a crash, if it ever does.
    """
    token = getattr(request.state, "share_token", None)
    if not token:
        raise HTTPException(status_code=404, detail="not found")
    return token


@router.get("/api/og.png", include_in_schema=False)
def share_match_png(request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    token = _share_token(request)
    return _png_response(state, token, build_match_card(state), None)


@router.get("/api/og/{slug}/{stage}.png", include_in_schema=False)
def share_stage_png(slug: str, stage: int, request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    token = _share_token(request)
    card = build_stage_card(state, slug, stage)
    if card is None:
        return _png_response(state, token, build_match_card(state), None)
    return _png_response(state, token, card, slug)
