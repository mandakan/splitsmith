"""``GET /api/looks`` and ``GET /api/looks/{name}/preview/{file}`` (spec
2026-10-06 section 4, issue #1246).

The catalog is :func:`splitsmith.looks.look_catalog`, read per request (a
user may drop a Look into ``~/.splitsmith/looks`` while the app runs). A
preview file is served only for an installed Look (or ``_shipped``, the
shipped default a user Look borrows from), by a bare
``<slot>-<variant>.png`` / ``.webp`` name inside that ``preview/``
directory; anything else is the same 404, which is what keeps the
``{file}`` parameter harmless hosted (``route_scope.HOSTED_CONFINED_ROUTES``).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..looks import PREVIEW_DIR, look_catalog, preview_owner_root

router = APIRouter()

_FILE_RE = re.compile(r"^[a-z0-9_-]+\.(png|webp)$")
_MEDIA = {".png": "image/png", ".webp": "image/webp"}


@router.get("/api/looks")
def get_looks() -> dict[str, Any]:
    return {"looks": [info.model_dump() for info in look_catalog()]}


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


__all__ = ["router"]
