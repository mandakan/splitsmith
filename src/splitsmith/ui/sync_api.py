"""Hosted-only ``/api/sync/*`` routes: desktop match adopt + state-doc
mirror upserts (desktop-to-hosted sync MVP, doc 2026-08-07, #631).

A desktop client (Tasks 5+) authenticates with a bearer token (Task 2/3's
``DesktopTokenAuth``, which resolves to a normal tenant exactly like a
session) and pushes one match as a read-only mirror: it adopts (or
re-adopts, idempotently) the match row via ``POST /matches``, then
upserts the match doc, each shooter's project doc, and each stage's
audit doc via the three ``PUT .../docs/...`` routes below. This module
does not run detection or touch storage - it only writes rows through
``PostgresMatchStore``/``ProjectStateStore``, the same stores the hosted
UI itself reads.

A mirrored row's ``origin`` is "desktop" for as long as it lives; a
sync push can never touch a natively-created hosted match (``origin ==
"hosted"``) - every doc route 409s ``not_a_mirror`` against one, and the
create route 409s ``match_exists_hosted`` rather than silently adopting
it. This is a one-way push: the hosted side never writes back to the
desktop client.

Local mode (no ``SPLITSMITH_MODE=hosted``) has no meaning for a sync
push - a local install has no durable per-user storage to mirror into -
so every route 404s there, same guard idiom as the desktop-token
management routes and share links.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .. import match_model
from .project import MatchProject

if TYPE_CHECKING:
    from ..db.matches import PostgresMatchStore
    from ..db.project_state import ProjectStateStore

router = APIRouter(prefix="/api/sync")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SyncMatchCreate(BaseModel):
    """Body for ``POST /api/sync/matches``."""

    match_id: str
    name: str


class SyncMatchCreateResponse(BaseModel):
    """Response for ``POST /api/sync/matches``."""

    match_id: str
    origin: str


class SyncDocVersionResponse(BaseModel):
    """Response shared by all three doc-upsert routes."""

    version: int


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode.

    Imported lazily so that importing this module never requires the
    hosted-only db deps (sqlalchemy et al) - the local-slim install
    still imports and registers this router; every route just 404s at
    request time. Same idiom as the desktop-token management routes.
    """
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _current_user(request: Request) -> Any:
    """Read the user the outer ``_auth_gate`` middleware already resolved.

    Every ``/api/sync/*`` route sits behind that gate (it matches on the
    ``/api/`` prefix), so an unauthenticated request never reaches this
    dependency - the 401 already happened there. Kept as an explicit
    dependency for parity with the rest of the ``/api/me/*`` surface.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def _matches_store(request: Request) -> PostgresMatchStore:
    state = request.app.state.splitsmith_state
    store = state.matches_store
    if store is None:
        raise HTTPException(status_code=500, detail="match store unavailable")
    return store


def _project_state(request: Request) -> ProjectStateStore:
    state = request.app.state.splitsmith_state
    store = state.project_state
    if store is None:
        raise HTTPException(status_code=500, detail="project state store unavailable")
    return store


async def _resolve_mirror(request: Request, match_id: str) -> Any:
    """Load the match row, enforcing the shared mirror contract.

    404 when the match is unknown to this tenant (also the outcome for a
    match owned by a different user - the two are indistinguishable by
    design, same as every other per-user lookup in this codebase). 409
    ``not_a_mirror`` when the row exists but was never adopted by a sync
    push - a sync can never touch a native hosted match.
    """
    row = await _matches_store(request).get(match_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.origin != "desktop":
        raise HTTPException(status_code=409, detail="not_a_mirror")
    return row


async def _mirror_save(
    load: Callable[[], Awaitable[tuple[dict | None, int]]],
    save: Callable[[int], Awaitable[int]],
) -> int:
    """Unconditional last-write-wins upsert over the optimistic-lock store."""
    from ..db import StateConflictError

    _, version = await load()
    try:
        return await save(version)
    except StateConflictError:
        _, version = await load()
        return await save(version)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/matches", response_model=SyncMatchCreateResponse)
async def create_or_adopt_match(
    body: SyncMatchCreate,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMatchCreateResponse:
    """Adopt ``match_id`` as a desktop mirror, or refresh its name.

    Idempotent: re-posting the same ``match_id`` just updates the name
    (``PostgresMatchStore.upsert`` never changes ``origin`` on an
    existing row). 409 ``match_exists_hosted`` when the row already
    exists as a natively-created hosted match - a sync push must never
    silently reclassify one.
    """
    _hosted_gate()
    store = _matches_store(request)
    existing = await store.get(body.match_id)
    if existing is not None and existing.origin == "hosted":
        raise HTTPException(status_code=409, detail="match_exists_hosted")
    record = await store.upsert(body.match_id, body.name, f"matches/{body.match_id}", origin="desktop")
    return SyncMatchCreateResponse(match_id=record.match_id, origin=record.origin)


@router.put("/matches/{match_id}/docs/match", response_model=SyncDocVersionResponse)
async def put_match_doc(
    match_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert the match doc, validated against ``match_model.Match``."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    try:
        match_model.Match.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    store = _project_state(request)

    async def _load() -> tuple[dict | None, int]:
        return await store.load_match(match_id)

    async def _save(version: int) -> int:
        return await store.save_match(match_id, body, expected_version=version)

    version = await _mirror_save(_load, _save)
    return SyncDocVersionResponse(version=version)


@router.put("/matches/{match_id}/docs/project/{slug}", response_model=SyncDocVersionResponse)
async def put_project_doc(
    match_id: str,
    slug: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert one shooter's project doc, validated against ``MatchProject``."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    try:
        MatchProject.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    store = _project_state(request)

    async def _load() -> tuple[dict | None, int]:
        return await store.load_project(match_id, slug)

    async def _save(version: int) -> int:
        return await store.save_project(match_id, slug, body, expected_version=version)

    version = await _mirror_save(_load, _save)
    return SyncDocVersionResponse(version=version)


@router.put("/matches/{match_id}/docs/audit/{slug}/{stage_number}", response_model=SyncDocVersionResponse)
async def put_audit_doc(
    match_id: str,
    slug: str,
    stage_number: int,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert one stage's audit doc. Schemaless - stored as-is, no model."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    store = _project_state(request)

    async def _load() -> tuple[dict | None, int]:
        return await store.load_audit(match_id, slug, stage_number)

    async def _save(version: int) -> int:
        return await store.save_audit(match_id, slug, stage_number, body, expected_version=version)

    version = await _mirror_save(_load, _save)
    return SyncDocVersionResponse(version=version)
