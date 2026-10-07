"""``GET /api/whats-new`` and ``POST /api/whats-new/seen``.

Both modes: ``state.whats_new`` is the prefs file locally and the
account's row hosted (the auth gate pins the tenant first). A store with
nothing on the caller resolves on the first ``GET``: see
:func:`splitsmith.whats_new.first_seen`. This module must not import
``server``; it reaches state through ``request.app.state``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..whats_new import WhatsNewEntry, WhatsNewStore, first_seen, known_ids, load_entries

router = APIRouter()


class WhatsNewPayload(BaseModel):
    entries: list[WhatsNewEntry]
    #: Entry ids and ``chip:<key>`` dismissals this user has seen.
    seen: list[str]


class MarkSeenRequest(BaseModel):
    ids: list[str] = Field(max_length=200)


def _store(request: Request) -> WhatsNewStore:
    return request.app.state.splitsmith_state.whats_new


@router.get("/api/whats-new", response_model=WhatsNewPayload)
async def get_whats_new(request: Request) -> WhatsNewPayload:
    entries = load_entries()
    store = _store(request)
    seen = await store.get()
    if seen is None:
        has_matches = bool(await request.app.state.splitsmith_state.recent_projects.list())
        seen = await store.add(first_seen(entries, has_matches=has_matches))
    return WhatsNewPayload(entries=entries, seen=seen)


@router.post("/api/whats-new/seen", response_model=WhatsNewPayload)
async def mark_seen(req: MarkSeenRequest, request: Request) -> WhatsNewPayload:
    entries = load_entries()
    unknown = sorted(set(req.ids) - known_ids(entries))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown ids: {', '.join(unknown)}")
    seen = await _store(request).add(req.ids)
    return WhatsNewPayload(entries=entries, seen=seen)


__all__ = ["router"]
