"""``GET /api/looks`` and ``GET /api/looks/{name}/preview/{file}`` (spec
2026-10-06 section 4, issue #1246).

The catalog is :func:`splitsmith.looks.look_catalog`, read per request (a
user may drop a Look into ``~/.splitsmith/looks`` while the app runs). A
preview file is served only for an installed Look (or ``_shipped``, the
shipped default a user Look borrows from, or ``_transitions``, the xfade
families' loops, #1259), by a bare
``<slot>-<variant>.png`` / ``.webp`` name inside that ``preview/``
directory; anything else is the same 404, which is what keeps the
``{file}`` parameter harmless hosted (``route_scope.HOSTED_CONFINED_ROUTES``).

``GET / PUT / DELETE /api/looks/{name}`` read and write the caller's own
Looks through ``state.looks`` (issue #1263): the Looks folder locally, the
account's ``user_looks`` rows hosted (the auth gate pins the tenant and
its Looks provider before this router runs). A shipped Look is never one
of them: it is not returned, changed or deleted here.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..composition import XFADE_FAMILIES
from ..look_store import (
    FolderLookStore,
    LookStore,
    LookStoreError,
    StoredLook,
    StoredLookBody,
    body_from_manifest,
    check_name,
    is_shipped_name,
)
from ..looks import (
    PREVIEW_DIR,
    TRANSITIONS_OWNER,
    LookError,
    load_look,
    look_catalog,
    preview_owner_root,
    shipped_looks_dir,
)

router = APIRouter()

_FILE_RE = re.compile(r"^[a-z0-9_-]+\.(png|webp)$")
_MEDIA = {".png": "image/png", ".webp": "image/webp"}


class TransitionDirectionInfo(BaseModel):
    name: str
    kind: str


class TransitionFamilyInfo(BaseModel):
    """One xfade family as the gallery shows it (issue #1259): a tile, its
    directions (the first is what the tile selects) and a looping preview."""

    id: str
    label: str
    help: str
    preview: str | None
    directions: list[TransitionDirectionInfo]


def transition_catalog() -> list[TransitionFamilyInfo]:
    """``composition.XFADE_FAMILIES`` with each family's preview URL."""
    previews = shipped_looks_dir() / TRANSITIONS_OWNER / PREVIEW_DIR
    out: list[TransitionFamilyInfo] = []
    for family in XFADE_FAMILIES:
        file = f"{family.id}.webp"
        out.append(
            TransitionFamilyInfo(
                id=family.id,
                label=family.label,
                help=family.help,
                preview=(
                    f"/api/looks/{TRANSITIONS_OWNER}/preview/{file}" if (previews / file).is_file() else None
                ),
                directions=[TransitionDirectionInfo(name=d.name, kind=d.kind) for d in family.directions],
            )
        )
    return out


@router.get("/api/looks")
def get_looks() -> dict[str, Any]:
    return {
        "looks": [info.model_dump() for info in look_catalog()],
        "transitions": [info.model_dump() for info in transition_catalog()],
    }


@router.get("/api/looks/{name}/preview/{file}")
def get_look_preview(name: str, file: str) -> FileResponse:
    root = preview_owner_root(name) if _FILE_RE.match(file) else None
    if root is None:
        raise HTTPException(status_code=404, detail="not found")
    path = root / PREVIEW_DIR / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path, media_type=_MEDIA[path.suffix], headers={"Cache-Control": "public, max-age=3600"}
    )


def _store(request: Request) -> LookStore:
    return request.app.state.splitsmith_state.looks


def _name(name: str) -> str:
    try:
        return check_name(name)
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/api/looks/{name}", response_model=StoredLook)
async def get_own_look(name: str, request: Request) -> StoredLook:
    stored = await _store(request).get(_name(name))
    if stored is None:
        raise HTTPException(status_code=404, detail="not found")
    return stored


@router.put("/api/looks/{name}", response_model=StoredLook)
async def put_own_look(name: str, body: StoredLookBody, request: Request, response: Response) -> StoredLook:
    store = _store(request)
    created = await store.get(_name(name)) is None
    try:
        stored = await store.put(name, body)
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if created:
        response.status_code = 201
    return stored


@router.delete("/api/looks/{name}", status_code=204)
async def delete_own_look(name: str, request: Request) -> Response:
    store = _store(request)
    if await store.get(_name(name)) is None:
        raise HTTPException(status_code=404, detail="not found")
    await store.delete(name)
    return Response(status_code=204)


class DuplicateLookRequest(BaseModel):
    source: str


@router.post("/api/looks/{name}/duplicate", status_code=201, response_model=StoredLook)
async def duplicate_look(name: str, req: DuplicateLookRequest, request: Request) -> StoredLook:
    """A new Look ``name`` copied from the installed Look ``source`` (#1264).
    Locally the copy is a folder with the source's templates (``looks new
    --from``); hosted it is a manifest whose base is the source's shipped
    Look, its colours and styles copied."""
    store = _store(request)
    if await store.get(_name(name)) is not None:
        raise HTTPException(status_code=409, detail=f"a Look named {name!r} already exists")
    try:
        source = load_look(req.source)
    except LookError:
        raise HTTPException(status_code=404, detail=f"no Look named {req.source!r}") from None
    try:
        if isinstance(store, FolderLookStore):
            from ..look_tools import LookToolError, new_look

            try:
                new_look(name, from_look=req.source)
            except LookToolError as exc:
                raise LookStoreError(str(exc)) from None
            stored = await store.get(name)
            assert stored is not None  # new_look loads the folder before it returns
            return stored
        body = body_from_manifest(source.manifest)
        base = req.source if is_shipped_name(req.source) else body.base
        return await store.put(name, body.model_copy(update={"base": base}))
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


__all__ = ["router"]
