"""Export preset settings routes (spec 2026-09-15 s1).

Built-ins come from :data:`export_presets.BUILTIN_PRESETS` and lead the
list; the user's own follow, by name. Writes to a ``builtin:`` id are
403: a built-in can be applied and saved *as* a new preset, never
overwritten or deleted.

Works in both modes: ``state.export_presets`` is the JSON file store
locally and the per-user Postgres store hosted (the auth gate pins the
tenant before this router runs). This module must not import
``server``; it reaches state through ``request.app.state``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from ..export_presets import (
    BUILTIN_PRESETS,
    MAX_NAME_LENGTH,
    ExportPreset,
    ExportPresetBody,
    ExportPresetStore,
    is_builtin_id,
)

router = APIRouter()

NEW_ID = "new"


class ExportPresetList(BaseModel):
    presets: list[ExportPreset]


class PutExportPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    body: ExportPresetBody

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _store(request: Request) -> ExportPresetStore:
    return request.app.state.splitsmith_state.export_presets


@router.get("/api/settings/export-presets", response_model=ExportPresetList)
async def list_export_presets(request: Request) -> ExportPresetList:
    own = await _store(request).list()
    return ExportPresetList(presets=[*BUILTIN_PRESETS, *own])


@router.put("/api/settings/export-presets/{preset_id}", response_model=ExportPreset)
async def put_export_preset(
    preset_id: str, req: PutExportPresetRequest, request: Request, response: Response
) -> ExportPreset:
    if is_builtin_id(preset_id):
        raise HTTPException(status_code=403, detail="built-in presets cannot be changed")
    created = preset_id == NEW_ID
    preset = ExportPreset(
        preset_id=uuid.uuid4().hex if created else preset_id,
        name=req.name,
        updated_at=datetime.now(UTC),
        body=req.body,
    )
    await _store(request).put(preset)
    if created:
        response.status_code = 201
    return preset


@router.delete("/api/settings/export-presets/{preset_id}", status_code=204)
async def delete_export_preset(preset_id: str, request: Request) -> Response:
    if is_builtin_id(preset_id):
        raise HTTPException(status_code=403, detail="built-in presets cannot be deleted")
    store = _store(request)
    if not any(p.preset_id == preset_id for p in await store.list()):
        raise HTTPException(status_code=404, detail="not found")
    await store.delete(preset_id)
    return Response(status_code=204)
