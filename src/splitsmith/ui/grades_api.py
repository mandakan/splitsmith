"""The colour grade gallery's routes (``splitsmith.grade``).

``GET /api/grades`` lists the shipped presets, in both modes. Your own LUTs
(``GET /api/grades/user``, ``POST /api/grades``) are files on this machine,
so desktop only (``route_scope.LOCAL_ONLY_ROUTES``), like a Look's own fonts.
``GET /api/grades/preview`` draws a grade over the gallery's swatch scene in
Python, cached by what it draws; it never runs ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile

from .. import grade
from ..looks import LookError, load_look
from ..runtime import runtime

router = APIRouter()


def _label(slug: str) -> str:
    return slug.replace("-", " ").capitalize()


def _preview_url(choice: str) -> str:
    return f"/api/grades/preview?grade={choice}"


@router.get("/api/grades")
def list_grades() -> dict[str, Any]:
    """The shipped presets, each with its gallery thumbnail."""
    return {
        "presets": [
            {"id": f"preset:{name}", "label": _label(name), "preview": _preview_url(f"preset:{name}")}
            for name in grade.preset_names()
        ]
    }


@router.get("/api/grades/user")
def list_user_grades() -> dict[str, Any]:
    """The LUTs you brought (desktop only)."""
    return {
        "user": [
            {"id": f"user:{name}", "label": name.rsplit("-", 1)[0], "preview": _preview_url(f"user:{name}")}
            for name in grade.list_user_luts()
        ]
    }


@router.post("/api/grades", status_code=201)
def upload_grade(file: Annotated[UploadFile, File()]) -> dict[str, str]:
    """Keep a ``.cube`` of your own (``grade.add_user_lut_bytes``): read and
    checked here, named by its bytes. 413 past the size cap, 422 for a file
    that is not a 3D LUT this reads."""
    data = file.file.read(grade.MAX_BYTES + 1)
    if len(data) > grade.MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"The LUT is larger than {grade.MAX_BYTES // 2**20} MB.")
    try:
        name = grade.add_user_lut_bytes(data, Path(file.filename or "lut").stem)
    except grade.GradeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    choice = f"user:{name}"
    return {"id": choice, "label": name.rsplit("-", 1)[0], "preview": _preview_url(choice)}


@router.get("/api/grades/preview")
def grade_preview(
    choice: Annotated[str, Query(alias="grade", max_length=80)] = "none",
    look: Annotated[str, Query(max_length=64)] = "splitsmith",
    strength: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> Response:
    """A grade (``look`` is that Look's own) over the swatch scene, as PNG;
    404 for a grade naming nothing real."""
    try:
        resolved = grade.resolve_grade(
            choice, load_look(look) if choice == "look" else None, strength=strength
        )
    except (grade.GradeError, LookError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    png = grade.preview_png(resolved, cache_dir=runtime().cache_dir / "grade-previews")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
