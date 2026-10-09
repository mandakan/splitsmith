"""You, your brand and the shooter book (spec 2026-10-08): the account's own
routes. "You" itself is the existing ``/api/me/scoreboard-identity``.

Both modes: ``state.account_profile`` and ``state.shooter_book`` are the local
files locally and the account's stores hosted (an empty, read-only store until
those exist, answered here as 503 on a write). None of these paths is on a
share allowlist: a share link never reads an account. This module must not
import ``server``; it reaches state through ``request.app.state``.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..account_profile import AccountProfile, AccountProfileStore, EmptyAccountProfileStore
from ..identity import CLUB_MAX_CHARS, LOGO_MAX_BYTES, ShooterIdentity
from ..look_brand import BrandError
from ..looks import BRAND_LINE_MAX, LookBrand
from ..shooter_book import EmptyShooterBookStore, ShooterBookEntry, ShooterBookStore, is_set

logger = logging.getLogger(__name__)

router = APIRouter()

_MEDIA = {".png": "image/png", ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".webp": "image/webp"}
_NOT_YET = "Your brand and the shooter book are not available here yet."


def _profile(request: Request) -> AccountProfileStore:
    return request.app.state.splitsmith_state.account_profile


def _book(request: Request) -> ShooterBookStore:
    return request.app.state.splitsmith_state.shooter_book


def _writable(store: object) -> None:
    if isinstance(store, (EmptyAccountProfileStore, EmptyShooterBookStore)):
        raise HTTPException(status_code=503, detail=_NOT_YET)


def _file(path, *, missing: str) -> FileResponse:  # type: ignore[no-untyped-def]
    media = _MEDIA.get(path.suffix.lower()) if path is not None else None
    if path is None or media is None:
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(
        path,
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"},
    )


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"logo is over {LOGO_MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    return data


# --- your brand -------------------------------------------------------------------


class ProfileBody(BaseModel):
    """``PUT /api/me/profile``: the brand's line (the logo has its own route)."""

    brand_line: str = Field(default="", max_length=BRAND_LINE_MAX)


def _profile_json(profile: AccountProfile) -> dict[str, object]:
    brand = profile.brand or LookBrand()
    return {"brand": {"logo": brand.logo, "line": brand.line}}


@router.get("/api/me/profile")
async def get_profile(request: Request) -> JSONResponse:
    return JSONResponse(_profile_json(await _profile(request).load()))


@router.put("/api/me/profile")
async def put_profile(body: ProfileBody, request: Request) -> JSONResponse:
    store = _profile(request)
    _writable(store)
    profile = await store.load()
    current = profile.brand or LookBrand()
    profile = profile.model_copy(
        update={"brand": current.model_copy(update={"line": body.brand_line.strip()})}
    )
    await store.save(profile)
    return JSONResponse(_profile_json(profile))


@router.post("/api/me/profile/brand-logo")
async def upload_brand_logo(request: Request, file: Annotated[UploadFile, File()]) -> JSONResponse:
    store = _profile(request)
    _writable(store)
    data = await _read_upload(file)
    try:
        name = await store.put_brand_logo(data)
    except BrandError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    profile = await store.load()
    current = profile.brand or LookBrand()
    profile = profile.model_copy(update={"brand": current.model_copy(update={"logo": name})})
    await store.save(profile)
    return JSONResponse(_profile_json(profile))


@router.delete("/api/me/profile/brand-logo")
async def remove_brand_logo(request: Request) -> JSONResponse:
    store = _profile(request)
    _writable(store)
    profile = await store.load()
    if profile.brand is not None:
        profile = profile.model_copy(update={"brand": profile.brand.model_copy(update={"logo": None})})
        await store.save(profile)
    return JSONResponse(_profile_json(profile))


@router.get("/api/me/profile/brand-logo")
async def get_brand_logo(request: Request) -> FileResponse:
    store = _profile(request)
    brand = (await store.load()).brand
    path = await store.brand_file(brand.logo) if brand is not None and brand.logo else None
    return _file(path, missing="no brand logo")


# --- the shooter book --------------------------------------------------------------


class BookEntryBody(BaseModel):
    """``PUT /api/me/shooter-book/{shooter_id}``: the accent and club line;
    only the keys sent are applied, ``null`` clears one."""

    accent: str | None = None
    club: str | None = Field(default=None, max_length=CLUB_MAX_CHARS * 2)
    label: str | None = None


async def _put_or_drop(store: ShooterBookStore, entry: ShooterBookEntry) -> None:
    """Store ``entry``, or remove it when it sets nothing: an empty entry
    would read as "from your shooter book" over nothing."""
    if is_set(entry.identity):
        await store.put(entry)
    else:
        await store.delete(entry.shooter_id)


def _entry_json(entry: ShooterBookEntry) -> dict[str, object]:
    return {
        "shooter_id": entry.shooter_id,
        "label": entry.label,
        "identity": entry.identity.model_dump(mode="json"),
        "updated_at": entry.updated_at.isoformat(),
    }


@router.get("/api/me/shooter-book")
async def list_shooter_book(request: Request) -> JSONResponse:
    return JSONResponse({"entries": [_entry_json(e) for e in await _book(request).list()]})


@router.put("/api/me/shooter-book/{shooter_id}")
async def put_shooter_book_entry(shooter_id: int, body: BookEntryBody, request: Request) -> JSONResponse:
    store = _book(request)
    _writable(store)
    current = await store.get(shooter_id)
    identity = current.identity if current is not None else ShooterIdentity()
    fields = body.model_dump(exclude_unset=True)
    try:
        identity = ShooterIdentity(
            accent=fields.get("accent", identity.accent),
            club=fields.get("club", identity.club),
            logo=identity.logo,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    entry = ShooterBookEntry(
        shooter_id=shooter_id,
        identity=identity,
        label=fields.get("label", current.label if current is not None else None),
    )
    await _put_or_drop(store, entry)
    return JSONResponse(_entry_json(entry))


@router.delete("/api/me/shooter-book/{shooter_id}")
async def delete_shooter_book_entry(shooter_id: int, request: Request) -> JSONResponse:
    store = _book(request)
    _writable(store)
    await store.delete(shooter_id)
    return JSONResponse({"ok": True})


@router.post("/api/me/shooter-book/{shooter_id}/logo")
async def upload_shooter_book_logo(
    shooter_id: int, request: Request, file: Annotated[UploadFile, File()]
) -> JSONResponse:
    store = _book(request)
    _writable(store)
    data = await _read_upload(file)
    try:
        name = await store.put_logo(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    current = await store.get(shooter_id)
    identity = (current.identity if current is not None else ShooterIdentity()).model_copy(
        update={"logo": name}
    )
    entry = ShooterBookEntry(
        shooter_id=shooter_id, identity=identity, label=current.label if current else None
    )
    await store.put(entry)
    return JSONResponse(_entry_json(entry))


@router.delete("/api/me/shooter-book/{shooter_id}/logo")
async def remove_shooter_book_logo(shooter_id: int, request: Request) -> JSONResponse:
    store = _book(request)
    _writable(store)
    current = await store.get(shooter_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no such entry")
    entry = current.model_copy(update={"identity": current.identity.model_copy(update={"logo": None})})
    await _put_or_drop(store, entry)
    return JSONResponse(_entry_json(entry))


@router.get("/api/me/shooter-book/{shooter_id}/logo")
async def get_shooter_book_logo(shooter_id: int, request: Request) -> FileResponse:
    store = _book(request)
    entry = await store.get(shooter_id)
    name = entry.identity.logo if entry is not None else None
    return _file(await store.logo_file(name) if name else None, missing="no logo")


# --- finding yourself ----------------------------------------------------------------


@router.get("/api/me/shooter-search")
def shooter_search(q: str = Query("", max_length=80)) -> JSONResponse:
    """Find shooters by name in the live shooter index, with no match open:
    how the "You" page pins your SSI shooter id."""
    from .scoreboard.http import ScoreboardError, SsiHttpClient

    if not q.strip():
        return JSONResponse([])
    try:
        with SsiHttpClient() as client:
            refs = client.find_shooter(q.strip())
    except ScoreboardError as exc:
        raise HTTPException(status_code=502, detail="The shooter index could not be reached.") from exc
    return JSONResponse([ref.model_dump(mode="json") for ref in refs])


__all__ = ["router"]


# --- the Shooters page ---------------------------------------------------------------


@router.get("/api/me/shooters")
async def list_shooters(request: Request) -> JSONResponse:
    """Everyone you have filmed (spec 2026-10-09): one row per SSI shooter id
    with the look the videos draw (the book's, else the newest match's own),
    one per match for a shooter without an id; you first. Writes nothing."""
    from . import shooter_roster
    from .server import _hosted_mode_active

    state = request.app.state.splitsmith_state
    if _hosted_mode_active():
        seen = await shooter_roster.hosted_seen(state.matches_store, state.project_state)
    else:
        seen = shooter_roster.local_seen(shooter_roster.local_roots())
    you = await state.scoreboard_identity.load()
    book = await _book(request).snapshot()
    rows = shooter_roster.build_roster(seen, book, you.shooter_id if you is not None else None)
    return JSONResponse({"rows": [row.to_json() for row in rows]})
