"""Hosted-only share-card routes: the card PNGs served on the anonymous
share surface (spec 2026-08-09).

Same lazy-import, always-registered idiom as ``sync_api`` and
``device_auth_api``: db and rendering imports stay inside function
bodies so a local-slim install still imports this module, and every
route 404s outside hosted mode.

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
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..audit_data import audit_shots_to_engine_shots
from ..overlay_theme import load_theme
from ..share_card import MatchCard, RosterEntry, StageCard, stage_figures
from ..share_card_render import cached_card_png

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
        stage_time=stg.time_seconds,
        figures=figures,
    )


_PNG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _chromium_factory() -> Any:
    from ..overlay_raster import ChromiumRasterizer

    return ChromiumRasterizer()


def _png_response(state: Any, token: str, card: MatchCard | StageCard, slug: str | None) -> Response:
    if state.storage is None:
        raise HTTPException(status_code=503, detail="share card rendering is hosted-mode only")
    data = cached_card_png(
        card,
        token=token,
        storage=state.storage,
        theme=load_theme("splitsmith"),
        # Passed as a factory, not an instance: a cache hit must not pay
        # Chromium's ~1s launch. Lazy import keeps a local-slim install
        # from importing playwright at module load.
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
